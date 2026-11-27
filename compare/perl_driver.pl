#!/usr/bin/perl
# Voert een scenario uit tegen de ORIGINELE SQL-Ledger Perl-code (SL/GL.pm, SL/CA.pm)
# en schrijft de resultaten van leesacties als JSON naar STDOUT.
#
# gebruik: perl perl_driver.pl <sql-ledger-dir> <dbname> <scenario.json>
#
# De controles uit het scherm (bin/mozilla/gl.pl 'post' + knoplogica) zijn hier
# nagebouwd, zodat foutgevallen vergeleken kunnen worden.

use strict;
no strict 'refs';
use warnings;
no warnings;
use JSON::PP;
use DBI;
binmode STDOUT, ":encoding(UTF-8)";   # DBD::Pg levert tekens, geen bytes

my ($sldir, $dbname, $scenario_file) = @ARGV;
@ARGV = ();
unshift @INC, $sldir;
chdir $sldir;

require SL::Form;
require SL::GL;
require SL::CA;
require SL::RP;
require SL::AM;
require SL::AA;
require SL::CP;

open my $fh, '<', $scenario_file or die "$scenario_file: $!";
my $scenario = decode_json(do { local $/; <$fh> });
close $fh;

my $cfg = $scenario->{config};
my %myconfig = (
  dbconnect    => "dbi:Pg:dbname=$dbname;host=127.0.0.1;port=55432",
  dbuser       => 'postgres',
  dbpasswd     => '',
  dbdriver     => 'Pg',
  dboptions    => "set DateStyle to 'ISO'",
  numberformat => $cfg->{numberformat},
  dateformat   => 'yyyy-mm-dd',
  countrycode  => $cfg->{countrycode} // '',
  timeout      => 3600,
);

my %saved;
my @results;

sub resolve { my $v = shift; return (defined $v && $v =~ /^\$(.+)/) ? $saved{$1} : $v }

sub fmt {    # canoniek bedrag ("1234.5") -> invoer in numberformat
  my $v = shift;
  return '' unless defined $v && $v ne '';
  my $nf = $myconfig{numberformat};
  if ($nf eq '1.000,00' || $nf eq '1000,00') { $v =~ s/\./,/ }
  $v;
}

sub new_form {
  my $form = bless {}, 'Form';
  $form->{login} = $cfg->{login};
  $form->{version} = '3.2.12';
  $form->{dbversion} = '3.2.4';
  $form;
}

sub defaultcurrency {
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
  my ($c) = $dbh->selectrow_array("SELECT curr FROM curr ORDER BY rn");
  $dbh->disconnect;
  $c;
}

sub check_post {    # bin/mozilla/gl.pl sub post + form_footer
  my ($form, $t) = @_;
  die "Error: Transaction Date missing!\n" unless $form->{transdate};
  my $dbh = $form->dbconnect(\%myconfig);
  my %d = $form->get_defaults($dbh, \@{[qw(closedto revtrans precision)]});
  my $closedto = $form->datetonum(\%myconfig, $d{closedto});
  my $transdate = $form->datetonum(\%myconfig, $form->{transdate});
  if ($form->{id}) {
    my ($old) = $dbh->selectrow_array("SELECT transdate FROM gl WHERE id = $form->{id}");
    if (defined $old) {
      die "Error: locked\n" if $d{revtrans};
      die "Error: locked\n" if $form->datetonum(\%myconfig, $old) <= $closedto;
    }
  }
  $dbh->disconnect;
  die "Error: Cannot post transaction for a closed period!\n" if $transdate <= $closedto;
  my ($debit, $credit) = (0, 0);
  for my $i (1 .. $form->{rowcount}) {
    my $dr = $form->parse_amount(\%myconfig, $form->{"debit_$i"});
    my $cr = $form->parse_amount(\%myconfig, $form->{"credit_$i"});
    die "Error: Cannot post transaction with a debit and credit entry for the same account!\n" if $dr && $cr;
    $debit += $dr; $credit += $cr;
  }
  die "Error: Out of balance transaction!\n"
    if $form->round_amount($debit, $d{precision}) != $form->round_amount($credit, $d{precision});
}


sub run_report {
  my ($report, $op, $res) = @_;
      my $a = $op->{args} || {};
  my $setup = sub {
    my $form = new_form();
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'rp.pl';
    $form->{defaultcurrency} = defaultcurrency();
    $form->{currency} = $form->{defaultcurrency};
    $form->{language_code} = $myconfig{countrycode};
    $form->{l_heading} = 1;
    $form->{l_subtotal} = 1;
    $form->{category} = 'X';
    for (keys %$a) { $form->{$_} = $a->{$_} }
    $form->{department} = "x--$a->{department_id}" if $a->{department_id};
    $form->{projectnumber} = "x--$a->{project_id}" if $a->{project_id};
    for (qw(l_heading l_subtotal)) { delete $form->{$_} if exists $a->{$_} && !$a->{$_} }
    $form;
  };
  my $locale = new Locale '', 'rp';

  # 1. ruwe data uit RP.pm
  my $form = $setup->();
  my %cfg = %myconfig;
  if ($report eq 'trial_balance') {
    RP->trial_balance(\%cfg, $form);
    $res->{rows} = $form->{TB} || [];
  } else {
    RP->$report(\%cfg, $form, $locale);
    $res->{data} = { map { $_ => $form->{$_} } qw(I E A L Q) };
    $res->{period} = $form->{period};
    $res->{exchangerate} = $form->{exchangerate};
  }

  # 2. het scherm uit rp.pl (bevat de berekende subtotalen en totalen)
  $main::form = $setup->();
  %main::myconfig = %myconfig;
  $main::locale = $locale;
  unless ($main::rp_loaded++) {
    package main;
    require "bin/mozilla/rp.pl";
  }
  my $html = '';
  open my $out, '>', \$html;
  my $old = select $out;
  eval { &{"main::generate_$report"}(); 1 } or do { select $old; die $@ };
  select $old;
  close $out;
  $res->{html} = $html;
    
}

sub run_arap_report {
  my ($report, $op, $res) = @_;
  my $a = $op->{args} || {};
  my $locale = new Locale '', 'rp';
  my $setup = sub {
    my $form = new_form();
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'rp.pl';
    $form->{defaultcurrency} = defaultcurrency();
    for (keys %$a) { $form->{$_} = $a->{$_} unless ref $a->{$_} }
    $form->{department} = "x--$a->{department_id}" if $a->{department_id};
    if ($report eq 'aging') {
      $form->{arap} = $a->{arap} || 'ar';
      $form->{vc} = $form->{arap} eq 'ar' ? 'customer' : 'vendor';
      for (@{ $a->{buckets} || [qw(c0 c15 c30 c45 c60 c75 c90)] }) { $form->{$_} = 1 }
      $form->{$form->{vc}} = $a->{name} if $a->{name};
      $form->{"$form->{vc}number"} = $a->{vcnumber} if $a->{vcnumber};
      $form->{"$form->{vc}_id"} = $a->{vc_id} if $a->{vc_id};
    }
    if ($report eq 'reminder') {
      $form->{vc} = 'customer';
      $form->{customer} = $a->{customer_id} ? "x--$a->{customer_id}" : ($a->{name} || '');
      $form->{customernumber} = $a->{customernumber} if $a->{customernumber};
    }
    if ($report eq 'tax_report') {
      $form->{db} = $a->{db} || 'ar';
      $form->{vc} = $form->{db} eq 'ar' ? 'customer' : 'vendor';
      $form->{taxaccounts} = join ' ', @{ $a->{accounts} || [] };
      $form->{gifi_taxaccounts} = join ' ', @{ $a->{gifi_accounts} || [] };
      for (@{ $a->{accounts} || [] }) { $form->{"accno_$_"} = 1 }
      for (@{ $a->{gifi_accounts} || [] }) { $form->{"gifi_$_"} = 1 }
      $form->{reportcode} = 'nontaxable' if $a->{nontaxable};
      for (qw(id transdate invnumber description name customernumber vendornumber address country taxnumber netamount tax total)) { $form->{"l_$_"} = 'Y' }
      $form->{l_subtotal} = $a->{l_subtotal} ? 'Y' : '';
    }
    if ($report eq 'payments') {
      $form->{db} = $a->{db} || 'ar';
      $form->{vc} = $form->{db} eq 'ar' ? 'customer' : 'vendor';
      $form->{paymentaccounts} = join ' ', @{ $a->{accounts} || [] };
      $form->{$form->{vc}} = $a->{name} if $a->{name};
      $form->{"$form->{vc}number"} = $a->{vcnumber} if $a->{vcnumber};
      for (qw(transdate reference description name customernumber vendornumber paid source memo curr till employee)) { $form->{"l_$_"} = 'Y' }
    }
    $form;
  };

  # 1. ruwe data uit RP.pm
  my $form = $setup->();
  my %cfg = %myconfig;
  if ($report eq 'aging') {
    RP->aging(\%cfg, $form);
    $res->{rows} = $form->{AG} || [];
  } elsif ($report eq 'reminder') {
    RP->reminder(\%cfg, $form);
    $res->{rows} = $form->{AG} || [];
  } elsif ($report eq 'tax_report') {
    RP->tax_report(\%cfg, $form);
    $res->{rows} = $form->{TR} || [];
  } else {
    RP->payments(\%cfg, $form);
    $res->{accounts} = [ map { { %$_, payments => ($form->{$_->{id}} || []) } } @{ $form->{PR} || [] } ];
  }

  # 2. scherm uit rp.pl
  $main::form = $setup->();
  %main::myconfig = %myconfig;
  $main::locale = $locale;
  { package main; require "bin/mozilla/rp.pl"; }
  my %entry = (aging => ($main::form->{arap} eq 'ap' ? 'generate_ap_aging' : 'generate_ar_aging'),
               reminder => 'generate_reminder', tax_report => 'generate_tax_report',
               payments => 'list_payments');
  my $html = '';
  open my $out, '>', \$html;
  my $old = select $out;
  eval { &{"main::$entry{$report}"}(); 1 } or do { select $old; die $@ };
  select $old;
  close $out;
  $res->{html} = $html;
}

sub run_account_screen {   # ca.pl list_transactions (lopend saldo, subtotalen, totaal)
  my ($op, $res) = @_;
  my $a = $op->{args} || {};
  my $form = new_form();
  $form->{path} = 'bin/mozilla';
  $form->{script} = 'ca.pl';
  for (keys %$a) { $form->{$_} = $a->{$_} }
  $form->{accounttype} = 'gifi' if $a->{gifi_accno} && !$a->{accno};
  $form->{department} = "x--$a->{department_id}" if $a->{department_id};
  $form->{projectnumber} = "x--$a->{project_id}" if $a->{project_id};
  $form->{l_subtotal} = $a->{l_subtotal} ? 'Y' : '';
  $main::form = $form;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'ca';
  { package main; require "bin/mozilla/ca.pl"; }
  my $html = '';
  open my $out, '>', \$html;
  my $old = select $out;
  eval { main::list_transactions(); 1 } or do { select $old; die $@ };
  select $old;
  close $out;
  $res->{html} = $html;
}

sub run_gl_screen {   # gl.pl transactions (lopend saldo, splitledger, subtotalen, totaal)
  my ($op, $res) = @_;
  my $s = $op->{search} || {};
  my $form = new_form();
  $form->{path} = 'bin/mozilla';
  $form->{script} = 'gl.pl';
  $form->{category} = 'X';
  for (keys %$s) { $form->{$_} = $s->{$_} }
  $form->{department} = "x--$s->{department_id}" if $s->{department_id};
  $form->{l_splitledger} = 1 if $s->{splitledger};
  $form->{l_subtotal} = $s->{l_subtotal} ? 'Y' : '';
  $form->{amountfrom} = fmt($s->{amountfrom}) if $s->{amountfrom};
  $form->{amountto} = fmt($s->{amountto}) if $s->{amountto};
  # alleen bedragkolommen: html_class_rows leest bedragen
  $form->{flds} = 'debit=Debit=,credit=Credit=';
  $form->{l_debit} = $form->{l_credit} = 'Y';
  %GL::seen = (); %GL::balance = ();
  $main::form = $form;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'gl';
  { package main; require "bin/mozilla/gl.pl"; }
  my $html = '';
  open my $out, '>', \$html;
  my $old = select $out;
  eval { main::transactions(); 1 } or do { select $old; die $@ };
  select $old;
  close $out;
  $res->{html} = $html;
}

# ---------------------------------------------------------------- AR/AP via het scherm
sub fmtn { my $v = shift; return '' unless defined $v && $v ne ''; fmt($v) }

sub arap_form {
  my ($vc) = @_;
  my $form = new_form();
  $form->{path} = 'bin/mozilla';
  $form->{script} = ($vc eq 'customer') ? 'ar.pl' : 'ap.pl';
  $form->{vc} = $vc;
  $form->{ARAP} = ($vc eq 'customer') ? 'AR' : 'AP';
  $form;
}

sub arap_ui {
  my ($mode, $a, $res) = @_;     # mode: new | repost | get
  my $vc = $a->{vc};
  my $form = arap_form($vc);
  $main::form = $form;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'aa';
  { package main; require "bin/mozilla/arap.pl"; require "bin/mozilla/arapprn.pl"; require "bin/mozilla/aa.pl"; }
  # update eindigt met display_form, dat het formulier als HTML opmaakt (bedragen
  # als tekst, vinkjes als 'checked'); in de browser komt dat via de formulierpost
  # weer terug. Hier slaan we de opmaak over zodat update alleen rekent.
  { no warnings 'redefine'; *main::display_form = sub { }; }
  my $html = '';
  open my $out, '>', \$html;
  my $old = select $out;
  my $ok = eval {
    if ($mode eq 'new') {
      $form->{type} = $a->{type} || 'transaction';
      $form->{"${vc}_id"} = $a->{vc_id};
      $form->{transdate} = $a->{transdate};
      main::create_links();
      my $ARAP = $form->{ARAP};
      # het scherm vult rekeningen/projecten van de vorige boeking van deze relatie
      # voor; hier telt alleen de scenario-invoer
      for my $k (keys %$form) { delete $form->{$k} if $k =~ /^(projectnumber|${ARAP}_amount|amount|description)_\d+$/ }
      $form->{$ARAP} = "$a->{arap_accno}--x" if $a->{arap_accno};
      my $i = 0;
      for my $l (@{ $a->{lines} }) {
        $i++;
        $form->{"${ARAP}_amount_$i"} = "$l->{accno}--x";
        $form->{"amount_$i"} = fmtn($l->{amount});
        $form->{"description_$i"} = $l->{description} // '';
        $form->{"projectnumber_$i"} = "x--$l->{project_id}" if $l->{project_id};
      }
      $form->{rowcount} = $i + 1;
      for (qw(invnumber notes ponumber ordnumber description)) { $form->{$_} = $a->{$_} if defined $a->{$_} }
      $form->{taxincluded} = $a->{taxincluded} ? 1 : 0 if exists $a->{taxincluded};
      if ($a->{currency}) { $form->{currency} = $a->{currency}; }
      $form->{exchangerate} = fmtn($a->{exchangerate}) if $a->{exchangerate};
      $form->{department} = "x--$a->{department_id}" if $a->{department_id};
      if (exists $a->{terms}) { $form->{terms} = $a->{terms}; }
      $form->{pending} = 1 if $a->{pending};
      my $j = 0;
      for my $p (@{ $a->{payments} || [] }) {
        $j++;
        $form->{"${ARAP}_paid_$j"} = "$p->{accno}--x";
        $form->{"paid_$j"} = fmtn($p->{amount});
        $form->{"datepaid_$j"} = $p->{datepaid};     # olddatepaid leeg: nieuwe betaalregel
        $form->{"source_$j"} = $p->{source} // '';
        $form->{"memo_$j"} = $p->{memo} // '';
        $form->{"exchangerate_$j"} = fmtn($p->{exchangerate}) if $p->{exchangerate};
      }
      $form->{"${ARAP}_paid_" . ($j + 1)} = $form->{"${ARAP}_paid_" . ($j + 1)} || ($a->{bank_accno} ? "$a->{bank_accno}--x" : $form->{payment_accno});
      $form->{paidaccounts} = $j + 1;
      if (my $d = $a->{discount}) {
        $form->{"${ARAP}_discount_paid"} = "$d->{accno}--x";
        $form->{discount_paid} = fmtn($d->{amount});
        $form->{discount_datepaid} = $d->{datepaid};
        $form->{olddiscount_datepaid} = $d->{datepaid};
        $form->{discount_source} = $d->{source} // '';
      }
      $form->{cashdiscount} = $a->{cashdiscount} if defined $a->{cashdiscount};
      $form->{discountterms} = $a->{discountterms} if defined $a->{discountterms};
      main::update();
      $form->{callback} = '';
      main::post();
    } else {
      $form->{id} = $a->{id};
      main::create_links();
      if ($mode eq 'get') {
        my %r;
        for my $k (keys %$form) {
          next if ref $form->{$k} || $k =~ /^select|^all_|_links$|^acc_trans$/;
          $r{$k} = $form->{$k};
        }
        $res->{form} = \%r;
      } else {
        main::update();
        $form->{repost} = 1;
        $form->{callback} = '';
        main::post();
      }
    }
    1;
  };
  select $old;
  die $@ unless $ok;
  $res->{id} = $form->{id};
}

sub html_fields {
  # velden die een browser bij het versturen van dit scherm zou meesturen
  my ($html) = @_;
  my %f;
  my $dec = sub { my $v = shift; $v = '' unless defined $v; $v =~ s/&quot;/"/g; $v =~ s/&lt;/</g; $v =~ s/&gt;/>/g; $v =~ s/&#(\d+);/chr($1)/ge; $v =~ s/&amp;/&/g; $v };
  my $attr = sub {
    my ($tag, $name) = @_;
    return $dec->($1) if $tag =~ /\b$name\s*=\s*"([^"]*)"/is;
    return $dec->($1) if $tag =~ /\b$name\s*=\s*'([^']*)'/is;
    return $dec->($1) if $tag =~ /\b$name\s*=\s*([^\s>]+)/is;
    return undef;
  };
  while ($html =~ /(<input\b[^>]*>)/gis) {
    my $tag = $1;
    my $name = $attr->($tag, 'name') // next;
    next if $name eq 'action';
    my $type = lc($attr->($tag, 'type') // 'text');
    my $value = $attr->($tag, 'value');
    if ($type eq 'checkbox' || $type eq 'radio') {
      next unless $tag =~ /\schecked\b/i;
      $value = 'on' unless defined $value;
    }
    next if $type eq 'submit' || $type eq 'button';
    $f{$name} = $value // '';
  }
  while ($html =~ /<select\b([^>]*)>(.*?)<\/select>/gis) {
    my ($head, $body) = ($1, $2);
    my $name = $attr->("<x $head>", 'name') // next;
    my ($first, $sel);
    while ($body =~ /<option\b([^>]*)>([^<]*)/gis) {
      my ($oa, $text) = ($1, $2);
      my $v = $attr->("<x $oa>", 'value');
      $v = $dec->($text) unless defined $v;
      $first = $v unless defined $first;
      $sel = $v if $oa =~ /\bselected\b/i;
    }
    $f{$name} = $sel // $first // '';
  }
  while ($html =~ /<textarea\b([^>]*)>(.*?)<\/textarea>/gis) {
    my $name = $attr->("<x $1>", 'name') // next;
    $f{$name} = $dec->($2);
  }
  \%f;
}

sub cp_request {
  # één request op cp.pl in een eigen proces (zoals CGI); geeft de HTML terug
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'cp.pl';
    $form->{login} = $cfg->{login};
    $form->{callback} = '';
    $main::form = $form;
    { package main; require "bin/mozilla/cp.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    die $err unless $ok;
    $res->{html} = $html;
    $res->{source} = $form->{source};
  });
}

sub cp_ui {
  # betaalscherm cp.pl: payment -> relatie/datum -> rekening -> posten aanvinken -> post
  my ($a, $res) = @_;
  my $vc = $a->{vc};
  my $ARAP = ($vc eq 'customer') ? 'AR' : 'AP';
  %main::myconfig = (%myconfig, vclimit => 100000);
  $main::locale = new Locale '', 'cp';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = cp_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    return (html_fields($r->{html}), $r);
  };
  my ($f) = $step->({ type => ($vc eq 'customer') ? 'receipt' : 'check' }, 'payment');
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
  my ($name) = $dbh->selectrow_array("SELECT name FROM $vc WHERE id = ?", undef, $a->{vc_id});
  $dbh->disconnect;
  # 1. relatie en betaaldatum
  $f->{$vc} = "$name--$a->{vc_id}";
  $f->{datepaid} = $a->{datepaid};
  ($f) = $step->($f, 'update');
  # 2. rekeningen, valuta, kenmerk
  for my $k (keys %$f) {
    next unless $k =~ /^(${ARAP}_paid|${ARAP}_discount|$ARAP)$/;
  }
  $f->{"${ARAP}_paid"} = (grep { /^\Q$a->{payment_accno}\E--/ } split /\n/, unescape_sel($f->{"select${ARAP}_paid"}))[0] // "$a->{payment_accno}--x";
  $f->{"${ARAP}_discount"} = (grep { /^\Q$a->{discount_accno}\E--/ } split /\n/, unescape_sel($f->{"select${ARAP}_discount"}))[0] // "$a->{discount_accno}--x" if $a->{discount_accno};
  $f->{$ARAP} = (grep { /^\Q$a->{arap_accno}\E--/ } split /\n/, unescape_sel($f->{"select$ARAP"}))[0] // "$a->{arap_accno}--x" if $a->{arap_accno};
  $f->{currency} = $a->{currency} if $a->{currency};
  $f->{paymentmethod} = "x--$a->{paymentmethod_id}" if $a->{paymentmethod_id};
  ($f) = $step->($f, 'update');
  $f->{exchangerate} = fmtn($a->{exchangerate}) if $a->{exchangerate};
  # (het kenmerk wordt bij het kiezen van de betaalrekening overschreven; daarna invullen)
  $f->{source} = $a->{source} if defined $a->{source};
  $f->{memo} = $a->{memo} if defined $a->{memo};
  # 3. posten aanvinken
  my %want = map { (($_->{id} ? resolve($_->{id}) : $_->{invnumber}) => $_) } @{ $a->{rows} || [] };
  for my $i (1 .. $f->{rowcount}) {
    my $w = $want{$f->{"id_$i"}} || $want{$f->{"invnumber_$i"}} or next;
    $f->{"checked_$i"} = 'on';
    $f->{"paid_$i"} = fmtn($w->{paid}) if defined $w->{paid};
    $f->{"discount_$i"} = fmtn($w->{discount}) if defined $w->{discount} && exists $f->{"discount_$i"};
  }
  ($f) = $step->($f, 'update');
  $res->{screen} = [ map { my $i = $_; +{ map { $_ => $f->{"${_}_$i"} } qw(id invnumber due paid discount checked) } } 1 .. $f->{rowcount} ];
  $f->{amount} = fmtn($a->{amount}) if defined $a->{amount};
  $res->{amount} = $f->{amount};
  if ($a->{print}) {          # fase 4 stap 4b: cp.pl print_payment (Print op het scherm, vóór het boeken)
    my $p = print_request('cp.pl', { %$f, format => 'pdf', media => 'screen' }, 'print');
    die "$p->{error}\n" if $p->{error};
    die "geen sjabloonvariabelen (print_form niet bereikt)\n" unless $p->{vars};
    $res->{print_vars} = $p->{vars};
  }
  return if $a->{nopost};
  my (undef, $r) = $step->($f, 'post');
  $res->{source} = $r->{source};
}

sub cp_payments_ui {
  # betaalscherm voor meerdere relaties (cp.pl payments): datum/rekening -> lijst per relatie ->
  # relaties aanvinken, bedrag -> post (post_payments)
  my ($a, $res) = @_;
  my $vc = $a->{vc};
  my $ARAP = ($vc eq 'customer') ? 'AR' : 'AP';
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'cp';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = cp_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    return (html_fields($r->{html}), $r);
  };
  my ($f) = $step->({ type => ($vc eq 'customer') ? 'receipt' : 'check' }, 'payments');
  $f->{datepaid} = $a->{datepaid};        # andere datum: de open posten worden (opnieuw) gelezen
  $f->{"${ARAP}_paid"} = (grep { /^\Q$a->{payment_accno}\E--/ } split /\n/, unescape_sel($f->{"select${ARAP}_paid"}))[0] // "$a->{payment_accno}--x";
  $f->{"${ARAP}_discount"} = (grep { /^\Q$a->{discount_accno}\E--/ } split /\n/, unescape_sel($f->{"select${ARAP}_discount"}))[0] // "$a->{discount_accno}--x" if $a->{discount_accno};
  $f->{currency} = $a->{currency} if $a->{currency};
  ($f) = $step->($f, 'update');
  my %want = map { ($_->{vc_id} => $_) } @{ $a->{rows} || [] };
  for my $i (1 .. $f->{rowcount}) {
    my $w = $want{$f->{"${vc}_id_$i"}} or next;
    $f->{"checked_$i"} = 'on';
    $f->{"memo_$i"} = $w->{memo} if defined $w->{memo};
  }
  ($f) = $step->($f, 'update');
  for my $i (1 .. $f->{rowcount}) {
    my $w = $want{$f->{"${vc}_id_$i"}} or next;
    $f->{"paid_$i"} = fmtn($w->{paid}) if defined $w->{paid};
    $f->{"source_$i"} = $w->{source} if defined $w->{source};
  }
  $res->{screen} = [ map { my $i = $_; +{ vc_id => $f->{"${vc}_id_$i"}, map { $_ => $f->{"${_}_$i"} } qw(due paid source checked) } } 1 .. $f->{rowcount} ];
  if ($a->{print}) {          # fase 4 stap 4b: cp.pl print_payments (per relatie een document)
    local $main::print_collect = 1;
    my $p = print_request('cp.pl', { %$f, format => 'pdf', media => 'screen' }, 'print');
    die "$p->{error}\n" if $p->{error};
    $res->{print_vars} = $p->{all_vars} || [];
  }
  return if $a->{nopost};
  $step->($f, 'post');
}

sub gl_request {
  # één request op gl.pl in een eigen proces (zoals CGI); geeft de HTML en het id terug
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'gl.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    # gl.pl post roept na het bevestigingsscherm (repost) 'exit' aan; in CGI eindigt
    # daar het request. Hier vangen: exit vóór het compileren van gl.pl vervangen.
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/gl.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
  });
}

sub gl_ui {
  # memoriaalscherm gl.pl als browser: add/edit -> velden invullen -> post (-> repost: continue),
  # post_as_new, delete -> yes, new_number
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'gl';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = gl_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    my $f = html_fields($r->{html});
    delete $f->{callback};       # redirect doet anders exec
    return ($f, $r);
  };
  my ($f) = $a->{id} ? $step->({ id => $a->{id} }, 'edit')
                     : $step->({ fxadj => $a->{fxadj} ? 1 : '', transdate => $a->{transdate} }, 'add');
  my $mode = $a->{mode} || 'post';
  if ($mode eq 'delete') {
    my ($c) = $step->($f, 'delete');
    my (undef, $r) = $step->($c, 'yes');
    return;
  }
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
  $f->{transdate} = $a->{transdate} if defined $a->{transdate};
  for (qw(reference description notes)) { $f->{$_} = $a->{$_} if defined $a->{$_} }
  if (defined $a->{department_id}) {
    my ($d) = $dbh->selectrow_array("SELECT description FROM department WHERE id = ?", undef, $a->{department_id});
    $f->{department} = $a->{department_id} ? "$d--$a->{department_id}" : '';
  }
  $f->{currency} = $a->{currency} if $a->{currency};
  $f->{exchangerate} = fmt($a->{exchangerate}) if defined $a->{exchangerate};
  my @lines = @{ $a->{lines} || [] };
  my $n = @lines;
  my $rows = ($f->{rowcount} || 0) > $n ? $f->{rowcount} : $n;
  for my $i (1 .. $rows) {
    delete $f->{"${_}_$i"} for qw(accno debit credit source memo projectnumber fx_transaction);
  }
  for my $i (1 .. $n) {
    my $l = $lines[$i - 1];
    my ($desc) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $l->{accno});
    $f->{"accno_$i"} = "$l->{accno}--" . ($desc // '');
    $f->{"debit_$i"} = fmt($l->{debit});
    $f->{"credit_$i"} = fmt($l->{credit});
    $f->{"source_$i"} = $l->{source} // '';
    $f->{"memo_$i"} = $l->{memo} // '';
    if ($l->{project_id}) {
      my ($pn) = $dbh->selectrow_array("SELECT projectnumber FROM project WHERE id = ?", undef, $l->{project_id});
      $f->{"projectnumber_$i"} = "$pn--$l->{project_id}";
    }
    $f->{"fx_transaction_$i"} = 1 if $l->{fx};
  }
  $f->{rowcount} = $rows;
  $dbh->disconnect;
  if ($mode eq 'new_number') {
    my ($g) = $step->($f, 'new_number');
    $res->{reference} = $g->{reference};
    return;
  }
  my ($g, $r) = $step->($f, $mode eq 'post_as_new' ? 'post_as_new' : 'post');
  if ($g->{nextsub} && $g->{repost}) {       # "je wijzigt een bestaande boeking" -> Continue
    ($g, $r) = $step->($g, 'continue');
  }
  $res->{id} = $r->{id};
}

sub aa_request {
  # één request op ar.pl/ap.pl in een eigen proces (zoals CGI); geeft de HTML en het id terug
  my ($vc, $fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = ($vc eq 'customer') ? 'ar.pl' : 'ap.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/$form->{script}"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
    $res->{invnumber} = $form->{invnumber};
  });
}

sub aa_ui {
  # verkoop-/inkoopscherm aa.pl als browser: add/edit -> relatie kiezen (update) -> velden
  # invullen -> update -> post (-> repost: continue); post_as_new, delete -> yes, new_number
  my ($a, $res) = @_;
  my $vc = $a->{vc};
  my $ARAP = ($vc eq 'customer') ? 'AR' : 'AP';
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'aa';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = aa_request($vc, $fields, $action);
    die "$r->{error}\n" if $r->{error};
    if ($ENV{AA_DEBUG}) { open my $d, '>>', $ENV{AA_DEBUG}; print $d "===== $action IN: " . join(' ', map { "$_=$fields->{$_}" } grep { /customer|vendor|searchby/ } sort keys %$fields) . "\n$r->{html}\n"; close $d }
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoInactiveDestroy => 1 });
  my $desc = sub { my ($accno) = @_;
    my ($d) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $accno);
    "$accno--" . ($d // '') };
  my ($f) = $a->{id} ? $step->({ id => $a->{id} }, 'edit')
                     : $step->({ type => $a->{type} || 'transaction', transdate => $a->{transdate} }, 'add');
  my $mode = $a->{mode} || 'post';
  if ($mode eq 'delete') {
    my ($c) = $step->($f, 'delete');
    $step->($c, 'yes');
    $dbh->disconnect;
    return;
  }
  $f->{transdate} = $a->{transdate} if defined $a->{transdate};
  if ($a->{vc_id}) {
    my ($name, $number) = $dbh->selectrow_array("SELECT name, ${vc}number FROM $vc WHERE id = ?", undef, $a->{vc_id});
    die "relatie $a->{vc_id} niet gevonden: " . ($dbh->errstr // '') . " db=$myconfig{dbconnect}\n" unless defined $name;
    if ($f->{"select$vc"}) {
      $f->{$vc} = "$name--$a->{vc_id}";
    } else {
      $f->{$vc} = $name;        # zoals een gebruiker: naam typen, nummer laten staan
    }
    ($f) = $step->($f, 'update');
  }
  for (qw(invnumber description notes ordnumber ponumber dcn)) { $f->{$_} = $a->{$_} if defined $a->{$_} }
  $f->{terms} = $a->{terms} if defined $a->{terms};
  $f->{duedate} = $a->{duedate} if defined $a->{duedate};
  if (exists $a->{taxincluded}) { if ($a->{taxincluded}) { $f->{taxincluded} = 1 } else { delete $f->{taxincluded} } }
  if (defined $a->{department_id}) {
    my ($d) = $dbh->selectrow_array("SELECT description FROM department WHERE id = ?", undef, $a->{department_id});
    $f->{department} = $a->{department_id} ? "$d--$a->{department_id}" : '';
  }
  $f->{currency} = $a->{currency} if $a->{currency};
  $f->{exchangerate} = fmt($a->{exchangerate}) if defined $a->{exchangerate};
  $f->{$ARAP} = $desc->($a->{arap_accno}) if $a->{arap_accno};
  if ($a->{lines}) {
    my @lines = @{ $a->{lines} };
    my $n = @lines;
    for my $i (1 .. ($f->{rowcount} || 0)) { delete $f->{"${_}_$i"} for ('amount', "${ARAP}_amount", 'description', 'projectnumber') }
    for my $i (1 .. $n) {
      my $l = $lines[$i - 1];
      $f->{"amount_$i"} = fmt($l->{amount});
      $f->{"${ARAP}_amount_$i"} = $desc->($l->{accno});
      $f->{"description_$i"} = $l->{description} // '';
      if ($l->{project_id}) {
        my ($pn) = $dbh->selectrow_array("SELECT projectnumber FROM project WHERE id = ?", undef, $l->{project_id});
        $f->{"projectnumber_$i"} = "$pn--$l->{project_id}";
      }
    }
    $f->{rowcount} = $n + 1;
  }
  for my $accno (keys %{ $a->{taxes} || {} }) {
    $f->{"tax_$accno"} = fmt($a->{taxes}{$accno});
    delete $f->{"calctax_$accno"};
  }
  if ($a->{payments}) {
    my @p = @{ $a->{payments} };
    my $keep = $f->{"${ARAP}_paid_" . ($f->{paidaccounts} || 1)};
    for my $j (1 .. ($f->{paidaccounts} || 0)) { delete $f->{"${_}_$j"} for ('paid', 'datepaid', 'olddatepaid', 'source', 'memo', 'exchangerate', "${ARAP}_paid", 'cleared') }
    for my $j (1 .. @p) {
      my $p = $p[$j - 1];
      $f->{"paid_$j"} = fmt($p->{amount});
      $f->{"datepaid_$j"} = $p->{datepaid};
      $f->{"source_$j"} = $p->{source} // '';
      $f->{"memo_$j"} = $p->{memo} // '';
      $f->{"exchangerate_$j"} = fmt($p->{exchangerate}) if $p->{exchangerate};
      $f->{"${ARAP}_paid_$j"} = $desc->($p->{accno});
    }
    $f->{"${ARAP}_paid_" . (@p + 1)} = $keep;
    $f->{paidaccounts} = @p + 1;
  }
  if (my $d = $a->{discount}) {
    $f->{discount_paid} = fmt($d->{amount});
    $f->{discount_datepaid} = $d->{datepaid};
    $f->{discount_source} = $d->{source} // '';
    $f->{"${ARAP}_discount_paid"} = $desc->($d->{accno});
  }
  $f->{cashdiscount} = fmt($a->{cashdiscount}) if defined $a->{cashdiscount};
  $f->{discountterms} = $a->{discountterms} if defined $a->{discountterms};
  $dbh->disconnect;
  my ($g) = $step->($f, 'update');
  if ($mode eq 'new_number') {
    my ($h) = $step->($g, 'new_number');
    $res->{invnumber} = $h->{invnumber};
    return;
  }
  my ($h, $r) = $step->($g, $mode eq 'post_as_new' ? 'post_as_new' : 'post');
  if ($h->{nextsub} && $h->{repost}) {
    ($h, $r) = $step->($h, 'continue');
  }
  $res->{id} = $r->{id};
}

# ================================================================ is (facturen met artikelen, fase 3) — begin
sub is_request {
  # één request op is.pl/ir.pl in een eigen proces (zoals CGI)
  my ($vc, $fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = ($vc eq 'customer') ? 'is.pl' : 'ir.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/$form->{script}"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
    $res->{invnumber} = $form->{invnumber};
  });
}

my %IS_PAYFLD = map { $_ => 1 } qw(paid datepaid olddatepaid source memo exchangerate cleared paymentmethod vr_id AR_paid AP_paid);

sub is_ui {
  # factuurscherm is.pl/ir.pl als browser: add/edit -> relatie kiezen (update) -> artikelen toevoegen
  # (nummer in de lege regel + update) -> aantallen/prijzen -> update -> post (-> repost: continue);
  # delete -> yes
  my ($a, $res) = @_;
  my $vc = $a->{vc};
  my $ARAP = ($vc eq 'customer') ? 'AR' : 'AP';
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', ($vc eq 'customer') ? 'is' : 'ir';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = is_request($vc, $fields, $action);
    die "$r->{error}\n" if $r->{error};
    if ($ENV{IS_DEBUG}) { open my $d, '>>', $ENV{IS_DEBUG}; print $d "===== $action\n$r->{html}\n"; close $d }
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoInactiveDestroy => 1 });
  my $desc = sub { my ($accno) = @_;
    my ($d) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $accno);
    "$accno--" . ($d // '') };
  my ($f) = $a->{id} ? $step->({ id => $a->{id}, type => $a->{type} || 'invoice' }, 'edit')
                     : $step->({ type => $a->{type} || 'invoice', transdate => $a->{transdate} }, 'add');
  my $mode = $a->{mode} || 'post';
  if ($mode eq 'delete') {
    my ($c) = $step->($f, 'delete');
    $step->($c, 'yes');
    $dbh->disconnect;
    return;
  }
  $f->{transdate} = $a->{transdate} if defined $a->{transdate};
  if ($a->{vc_id}) {
    my ($name) = $dbh->selectrow_array("SELECT name FROM $vc WHERE id = ?", undef, $a->{vc_id});
    $f->{$vc} = $f->{"select$vc"} ? "$name--$a->{vc_id}" : $name;
    ($f) = $step->($f, 'update');
  }
  for (qw(invnumber description notes intnotes ordnumber ponumber shippingpoint shipvia waybill)) { $f->{$_} = $a->{$_} if defined $a->{$_} }
  if (exists $a->{taxincluded}) { if ($a->{taxincluded}) { $f->{taxincluded} = 1 } else { delete $f->{taxincluded} } }
  $f->{currency} = $a->{currency} if $a->{currency};
  $f->{exchangerate} = fmt($a->{exchangerate}) if defined $a->{exchangerate};
  $f->{$ARAP} = $desc->($a->{arap_accno}) if $a->{arap_accno};
  if (defined $a->{department_id}) {
    my ($d) = $dbh->selectrow_array("SELECT description FROM department WHERE id = ?", undef, $a->{department_id});
    $f->{department} = $a->{department_id} ? "$d--$a->{department_id}" : '';
  }
  ($f) = $step->($f, 'update') if $a->{currency} || $a->{transdate};
  my $setline = sub {
    my ($i, $l) = @_;
    $f->{"qty_$i"} = fmt($l->{qty}) if defined $l->{qty};
    $f->{"sellprice_$i"} = fmt($l->{sellprice}) if defined $l->{sellprice};
    $f->{"discount_$i"} = fmt($l->{discount}) if defined $l->{discount};
    for (qw(description serialnumber deliverydate itemnotes)) { $f->{"${_}_$i"} = $l->{$_} if defined $l->{$_} }
    if (defined $l->{project_id}) {
      my ($pn) = $dbh->selectrow_array("SELECT projectnumber FROM project WHERE id = ?", undef, $l->{project_id});
      $f->{"projectnumber_$i"} = $l->{project_id} ? "$pn--$l->{project_id}" : '';
    }
  };
  if ($a->{lines}) {
    # alle regels vervangen: bestaande leegmaken en artikelen een voor een toevoegen
    my $rc = $f->{rowcount} || 1;
    for my $k (keys %$f) {
      next unless $k =~ /^(.+)_(\d+)$/;
      next if $IS_PAYFLD{$1} || $2 > $rc;
      delete $f->{$k};
    }
    $f->{rowcount} = 1;
    for my $l (@{ $a->{lines} }) {
      my $i = $f->{rowcount};
      $f->{"partnumber_$i"} = $l->{partnumber};
      ($f) = $step->($f, 'update');
      die "artikel $l->{partnumber} niet (eenduidig) gevonden\n" if $f->{rowcount} != $i + 1;
      $setline->($i, $l);
    }
  }
  for my $i (keys %{ $a->{set} || {} }) { $setline->($i, $a->{set}{$i}) }    # bestaande regel wijzigen
  if ($a->{payments}) {
    my @p = @{ $a->{payments} };
    my $keep = $f->{"${ARAP}_paid_" . ($f->{paidaccounts} || 1)};
    for my $j (1 .. ($f->{paidaccounts} || 0)) { delete $f->{"${_}_$j"} for ('paid', 'datepaid', 'olddatepaid', 'source', 'memo', 'exchangerate', "${ARAP}_paid", 'cleared') }
    for my $j (1 .. @p) {
      my $p = $p[$j - 1];
      $f->{"paid_$j"} = fmt($p->{amount});
      $f->{"datepaid_$j"} = $p->{datepaid};
      $f->{"source_$j"} = $p->{source} // '';
      $f->{"memo_$j"} = $p->{memo} // '';
      $f->{"exchangerate_$j"} = fmt($p->{exchangerate}) if $p->{exchangerate};
      $f->{"${ARAP}_paid_$j"} = $desc->($p->{accno});
    }
    $f->{"${ARAP}_paid_" . (@p + 1)} = $keep;
    $f->{paidaccounts} = @p + 1;
    # update stelt bij een nieuwe betaaldatum de koers uit de tabel voor; daarna de eigen koers invullen
    if (grep { $_->{exchangerate} } @p) {
      ($f) = $step->($f, 'update');
      for my $j (1 .. @p) { $f->{"exchangerate_$j"} = fmt($p[$j - 1]->{exchangerate}) if $p[$j - 1]->{exchangerate} }
    }
  }
  $dbh->disconnect;
  my ($g) = $step->($f, 'update');
  my ($h, $r) = $step->($g, $mode eq 'post_as_new' ? 'post_as_new' : 'post');
  if ($h->{nextsub} && $h->{repost}) {
    ($h, $r) = $step->($h, 'continue');
  }
  $res->{id} = $r->{id};
}
# ===== einde is ==========================================================================

# ================================================================ print (documenten, fase 4) — begin
sub print_request {
  # één print-request op is.pl/ir.pl/oe.pl; Form::parse_template wordt onderschept zodat alle
  # sjabloonvariabelen ($form na print_form) als JSON terugkomen in plaats van een document
  my ($script, $fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = $script;
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { no warnings 'redefine';
      *Form::parse_template = sub {
        my ($self) = @_;
        my %d;
        for my $k (keys %$self) {
          my $v = $self->{$k};
          if (!ref $v) { $d{$k} = $v }
          elsif (ref $v eq 'ARRAY' && !grep { ref $_ } @$v) { $d{$k} = [ @$v ] }
        }
        if ($main::print_collect) {         # cp.pl print_payments: één document per relatie
          push @{ $res->{all_vars} }, \%d;
          return;
        }
        $res->{vars} = \%d;
        die "__PRINTED__\n";
      };
    }
    { package main; require "bin/mozilla/$form->{script}"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && ($err eq "__EXIT__\n" || $err eq "__PRINTED__\n");
    die $err unless $ok;
    $res->{html} = $html unless $res->{vars} || $res->{all_vars};
  });
}

sub print_ui {
  # document "op het scherm" zoals de knop Printen: edit -> formname/taal/sortering -> print
  my ($a, $res) = @_;
  my $script = $a->{script};
  %main::myconfig = (%myconfig, name => $cfg->{username} // 'Test', email => $cfg->{useremail} // 'test@example.com');
  $main::locale = new Locale '', ($script =~ /^(\w+)\.pl$/)[0];
  my $r = print_request($script, { id => $a->{id}, type => $a->{type} || 'invoice', vc => $a->{vc} }, 'edit');
  die "$r->{error}\n" if $r->{error};
  my $f = html_fields($r->{html});
  delete $f->{callback};
  $f->{formname} = $a->{formname};
  $f->{format} = 'txt';            # format_string laat tekst dan ongemoeid
  $f->{media} = 'screen';
  $f->{copies} = 1;
  $f->{language_code} = $a->{language_code} if defined $a->{language_code};
  for (qw(sortby groupprojectnumber grouppartsgroup)) { $f->{$_} = $a->{$_} if defined $a->{$_} }
  my $p = print_request($script, $f, 'print');
  die "$p->{error}\n" if $p->{error};
  die "geen sjabloonvariabelen (print_form niet bereikt)\n" unless $p->{vars};
  $res->{vars} = $p->{vars};
}

sub rp_print_ui {
  # aanmaningen en rekeningoverzichten (fase 4 stap 4a): rp.pl als browser.
  # reminder:  generate_reminder -> per factuur (ids) print met alleen die ndx_<id> (media screen)
  # statement: generate_ar_aging (todate, kolommen) -> per (valuta, klant) print met alleen die ndx
  # Geeft {rows => [{id|key, level, vars}], ids/keys} terug; rp.pl gebruikt vandaag als peildatum
  # voor aanmaningen (RP.pm: current_date).
  my ($a, $res) = @_;
  %main::myconfig = (%myconfig, name => $cfg->{username} // 'Test', email => $cfg->{useremail} // 'test@example.com');
  $main::locale = new Locale '', 'rp';
  my $type = $a->{type};
  my %base = (type => $type, reportcode => $type, format => 'txt', media => 'screen');
  my ($gen, @sel);
  if ($type eq 'reminder') {
    $gen = 'generate_reminder';
  } else {
    $gen = 'generate_ar_aging';
    $base{todate} = $a->{todate} if $a->{todate};
    $base{overdue} = 1 if $a->{overdue};
    $base{$_} = 1 for @{ $a->{buckets} || [qw(c0 c30 c60 c90)] };
  }
  my $r = print_request('rp.pl', { %base }, $gen);
  die "$r->{error}\n" if $r->{error};
  my $f = html_fields($r->{html});
  delete $f->{callback};
  my @rows;
  if ($type eq 'reminder') {
    my @ids = split / /, ($f->{ids} // '');
    for my $id (@ids) {
      my %g = (%$f, %base, formname => 'reminder');
      $g{"ndx_$_"} = '' for @ids;
      $g{"ndx_$id"} = 1;
      my $p = print_request('rp.pl', \%g, 'print');
      die "$p->{error}\n" if $p->{error};
      push @rows, { id => $id, level => $f->{"level_$id"}, vars => $p->{vars} };
    }
    $res->{ids} = \@ids;
  } else {
    my @vc = split / /, ($f->{vc_ids} // '');
    my @curr = split / /, ($f->{curr} // '');
    my @keys;
    for my $c (@curr) { for my $v (@vc) { push @keys, [$c, $v] if exists $f->{"ndx_${c}_$v"} || $r->{html} =~ /name="?ndx_${c}_$v"?/ } }
    for my $k (@keys) {
      my ($c, $v) = @$k;
      my %g = (%$f, %base, formname => 'statement');
      for my $c2 (@curr) { $g{"ndx_${c2}_$_"} = '' for @vc }
      $g{"ndx_${c}_$v"} = 1;
      my $p = print_request('rp.pl', \%g, 'print');
      die "$p->{error}\n" if $p->{error};
      push @rows, { key => "$v-$c", vars => $p->{vars} } if $p->{vars};
    }
    $res->{keys} = [ map { "$_->[1]-$_->[0]" } @keys ];
  }
  $res->{rows} = \@rows;
}
# ===== einde print =======================================================================

# ================================================================ oe (orders en offertes, fase 3) — begin
sub oe_request {
  # één request op oe.pl in een eigen proces (zoals CGI); "invoice" laadt zelf is.pl/ir.pl
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'oe.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/oe.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
  });
}

sub oe_ui {
  # orderscherm oe.pl als browser: add/edit -> relatie (update) -> artikelen (nummer in de lege regel +
  # update) -> aantallen/prijzen -> update -> save (bestaand: repost -> continue). mode delete -> yes;
  # mode convert: knop (sales_order/purchase_order/quotation/rfq) -> nieuw formulier -> update -> save;
  # mode invoice: verzonden aantallen (ship) -> knop factuur -> is.pl/ir.pl update -> post
  my ($a, $res) = @_;
  my $type = $a->{type};
  my $vc = ($type =~ /^(sales_order|sales_quotation)$/) ? 'customer' : 'vendor';
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'oe';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = oe_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    if ($ENV{OE_DEBUG}) { open my $d, '>>', $ENV{OE_DEBUG}; print $d "===== $action\n$r->{html}\n"; close $d }
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my $isstep = sub {
    my ($fields, $action) = @_;
    my $r = is_request($vc, $fields, $action);
    die "$r->{error}\n" if $r->{error};
    if ($ENV{OE_DEBUG}) { open my $d, '>>', $ENV{OE_DEBUG}; print $d "===== is $action\n$r->{html}\n"; close $d }
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoInactiveDestroy => 1 });
  my ($f) = $a->{id} ? $step->({ id => $a->{id}, type => $type }, 'edit')
                     : $step->({ type => $type, vc => $vc }, 'add');
  my $mode = $a->{mode} || 'save';
  if ($mode eq 'delete') {
    my ($c) = $step->($f, 'delete');
    $step->($c, 'yes');
    $dbh->disconnect;
    return;
  }
  $f->{transdate} = $a->{transdate} if defined $a->{transdate};
  if ($a->{vc_id}) {
    my ($name) = $dbh->selectrow_array("SELECT name FROM $vc WHERE id = ?", undef, $a->{vc_id});
    $f->{$vc} = $f->{"select$vc"} ? "$name--$a->{vc_id}" : $name;
    ($f) = $step->($f, 'update');
  }
  for (qw(ordnumber quonumber description notes intnotes ponumber shippingpoint shipvia waybill reqdate)) { $f->{$_} = $a->{$_} if defined $a->{$_} }
  if (exists $a->{taxincluded}) { if ($a->{taxincluded}) { $f->{taxincluded} = 1 } else { delete $f->{taxincluded} } }
  $f->{currency} = $a->{currency} if $a->{currency};
  $f->{exchangerate} = fmt($a->{exchangerate}) if defined $a->{exchangerate};
  if (defined $a->{department_id}) {
    my ($d) = $dbh->selectrow_array("SELECT description FROM department WHERE id = ?", undef, $a->{department_id});
    $f->{department} = $a->{department_id} ? "$d--$a->{department_id}" : '';
  }
  ($f) = $step->($f, 'update') if $a->{currency} || $a->{transdate};
  my $setline = sub {
    my ($i, $l) = @_;
    $f->{"qty_$i"} = fmt($l->{qty}) if defined $l->{qty};
    $f->{"sellprice_$i"} = fmt($l->{sellprice}) if defined $l->{sellprice};
    $f->{"discount_$i"} = fmt($l->{discount}) if defined $l->{discount};
    for (qw(description serialnumber reqdate itemnotes)) { $f->{"${_}_$i"} = $l->{$_} if defined $l->{$_} }
    if (defined $l->{project_id}) {
      my ($pn) = $dbh->selectrow_array("SELECT projectnumber FROM project WHERE id = ?", undef, $l->{project_id});
      $f->{"projectnumber_$i"} = $l->{project_id} ? "$pn--$l->{project_id}" : '';
    }
  };
  if ($a->{lines}) {
    my $rc = $f->{rowcount} || 1;
    for my $k (keys %$f) {
      next unless $k =~ /^(.+)_(\d+)$/;
      next if $2 > $rc || $1 =~ /^(paid|datepaid|olddatepaid|source|memo|exchangerate|cleared|paymentmethod|vr_id|AR_paid|AP_paid)$/;
      delete $f->{$k};
    }
    $f->{rowcount} = 1;
    for my $l (@{ $a->{lines} }) {
      my $i = $f->{rowcount};
      $f->{"partnumber_$i"} = $l->{partnumber};
      ($f) = $step->($f, 'update');
      die "artikel $l->{partnumber} niet (eenduidig) gevonden\n" if $f->{rowcount} != $i + 1;
      $setline->($i, $l);
    }
  }
  for my $i (keys %{ $a->{set} || {} }) { $setline->($i, $a->{set}{$i}) }
  ($f) = $step->($f, 'update');
  if ($mode eq 'convert') {
    # knop op een opgeslagen order/offerte: nieuw formulier (bron wordt nu al gesloten bij offerte -> order)
    ($f) = $step->($f, $a->{to});
    $f->{transdate} = $a->{new_transdate} if defined $a->{new_transdate};
    ($f) = $step->($f, 'update');
    my ($h, $r) = $step->($f, 'save');
    $res->{id} = $r->{id};
    $dbh->disconnect;
    return;
  }
  if ($mode eq 'invoice') {
    for my $i (keys %{ $a->{ship} || {} }) { $f->{"ship_$i"} = fmt($a->{ship}{$i}) }
    my ($g) = $step->($f, 'invoice');
    $g->{invnumber} = $a->{invnumber} if defined $a->{invnumber};
    ($g) = $isstep->($g, 'update');
    my ($h, $r) = $isstep->($g, 'post');
    $res->{id} = $r->{id};
    $dbh->disconnect;
    return;
  }
  $dbh->disconnect;
  my ($h, $r) = $step->($f, $mode eq 'save_as_new' ? 'save_as_new' : 'save');
  if ($h->{nextsub} && $h->{repost}) {
    ($h, $r) = $step->($h, 'continue');
  }
  $res->{id} = $r->{id};
}
# ===== einde oe ==========================================================================

# ================================================================ rc (afletteren, fase 2) — begin
sub rc_request {
  # één request op rc.pl in een eigen proces (zoals CGI); geeft de HTML terug
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'rc.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    %main::myconfig = %myconfig;
    $main::locale = new Locale '', 'rc';
    { no warnings; *Form::redirect = sub { };      # done: anders exec van het vervolgscherm
      *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/rc.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
  });
}

sub rc_first {
  # rc.pl get_payments met de keuze van het beginscherm
  my ($a) = @_;
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
  my ($desc) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $a->{accno});
  $dbh->disconnect;
  my %f = (accno => "$a->{accno}--" . ($desc // ''), fromdate => $a->{fromdate} // '', todate => $a->{todate} // '',
           summary => (defined $a->{summary} ? $a->{summary} : 1), fx_transaction => (($a->{fx} // 1) ? 1 : ''),
           report => $a->{report} ? 1 : '');
  my $r = rc_request(\%f, 'get_payments');
  die "$r->{error}\n" if $r->{error};
  $r->{html};
}

sub rc_ui {
  # afletterscherm rc.pl als browser: get_payments -> vinkjes (+ afschriftsaldo) -> update -> done
  my ($a, $res) = @_;
  my $html = rc_first($a);
  my $f = html_fields($html);
  my %want = map { ($_ => 1) } @{ $a->{check} || [] };
  my %unwant = map { ($_ => 1) } @{ $a->{uncheck} || [] };
  for my $i (1 .. $f->{rowcount}) {
    next unless exists $f->{"source_$i"};
    my ($src, $td) = ($f->{"source_$i"}, $f->{"transdate_$i"});
    $f->{"cleared_$i"} = 1 if $want{$src} || $want{"$td $src"};
    delete $f->{"cleared_$i"} if $unwant{$src} || $unwant{"$td $src"};
  }
  $f->{statementbalance} = fmt($a->{statementbalance}) if defined $a->{statementbalance} && $a->{statementbalance} ne 'auto';
  if ($a->{select_all}) {
    my $r = rc_request($f, 'select_all');
    die "$r->{error}\n" if $r->{error};
    $html = $r->{html};
    $f = html_fields($html);
    $f->{statementbalance} = fmt($a->{statementbalance}) if defined $a->{statementbalance} && $a->{statementbalance} ne 'auto';
  }
  unless ($a->{skip_update}) {
    my $r = rc_request($f, 'update');
    die "$r->{error}\n" if $r->{error};
    $html = $r->{html};
    $f = html_fields($html);
    if (($a->{statementbalance} // '') eq 'auto') {
      # afschriftsaldo = afgeletterd saldo (verschil 0), zoals een gebruiker het overneemt
      my $num = sub { my $v = shift // 0; my $nf = $myconfig{numberformat};
                      if ($nf =~ /,\d\d$/) { $v =~ s/\.//g; $v =~ s/,/./ } else { $v =~ s/,//g } $v };
      my $sb = $num->($f->{statementbalance}) + $num->($f->{difference});
      $f->{statementbalance} = fmt(sprintf('%.2f', $sb));
      $r = rc_request($f, 'update');
      die "$r->{error}\n" if $r->{error};
      $html = $r->{html};
      $f = html_fields($html);
    }
  }
  $res->{html} = $html;
  return if $a->{nosave};
  my $r = rc_request($f, 'done');
  $res->{save_error} = $r->{error} if $r->{error};
}
# ================================================================ rc (afletteren, fase 2) — einde

# ===== ct (relaties, fase 2): ct.pl als browser ==========================================
sub ct_request {
  # één request op ct.pl in een eigen proces (zoals CGI); geeft de HTML en het id terug
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'ct.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/ct.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
  });
}

sub ct_ui {
  # klant-/leveranciersscherm ct.pl als browser: add/edit -> (soort wisselen: update) -> velden
  # invullen -> save / save_as_new; delete (knop bij ongebruikte relatie); new_number
  my ($a, $res) = @_;
  my $vc = $a->{vc};
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'ct';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = ct_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my ($f) = $a->{id} ? $step->({ id => $a->{id}, db => $vc }, 'edit') : $step->({ db => $vc }, 'add');
  my $mode = $a->{mode} || 'save';
  if ($mode eq 'delete') {
    $step->($f, 'delete');
    return;
  }
  my %v = %{ $a->{fields} || {} };
  if (defined $v{typeofcontact} && $v{typeofcontact} ne ($f->{typeofcontact} // '')) {
    $f->{typeofcontact} = delete $v{typeofcontact};
    ($f) = $step->($f, 'update');
  }
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoInactiveDestroy => 1 });
  for my $k (keys %v) {
    my $x = $v{$k};
    if ($k eq 'number') { $f->{"${vc}number"} = $x }
    elsif ($k eq 'taxes') { delete $f->{$_} for grep { /^tax_\d/ && !/_description$/ } keys %$f; $f->{"tax_$_"} = 1 for @$x }
    elsif ($k =~ /^(taxincluded|remittancevoucher)$/) { if ($x) { $f->{$k} = 1 } else { delete $f->{$k} } }
    elsif ($k =~ /_accno$/ && $k ne 'gifi_accno') {
      my ($d) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $x);
      $f->{$k} = $x ? "$x--" . ($d // '') : '';
    }
    elsif ($k =~ /^(discount|cashdiscount|creditlimit|threshold)$/) { $f->{$k} = fmt($x) }
    else { $f->{$k} = $x }
  }
  $dbh->disconnect;
  if ($mode eq 'new_number') {
    my ($g) = $step->($f, 'new_number');
    $res->{number} = $g->{"${vc}number"};
    return;
  }
  my (undef, $r) = $step->($f, $mode eq 'save_as_new' ? 'save_as_new' : 'save');
  $res->{id} = $r->{id};
}
# ===== einde ct ==========================================================================
# ============================================================ blok "ic": artikelen (ic.pl als browser, fase 3)
sub ic_request {
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'ic.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/ic.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
  });
}

sub ic_ui {
  # artikelscherm ic.pl als browser: add (item=part|service) / edit -> velden -> save / save_as_new;
  # delete (knop bij ongebruikt artikel); new_number
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'ic';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = ic_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my ($f) = $a->{id} ? $step->({ id => $a->{id} }, 'edit') : $step->({ item => $a->{item} || 'part' }, 'add');
  my $mode = $a->{mode} || 'save';
  if ($mode eq 'delete') {
    $step->($f, 'delete');
    return;
  }
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoInactiveDestroy => 1 });
  my %v = %{ $a->{fields} || {} };
  for my $k (keys %v) {
    my $x = $v{$k};
    if ($k eq 'taxes') { delete $f->{$_} for grep { /^IC_tax_\d+$/ } keys %$f; $f->{"IC_tax_$_"} = 1 for @$x }
    elsif ($k =~ /^(obsolete|checkinventory)$/) { if ($x) { $f->{$k} = 1 } else { delete $f->{$k} } }
    elsif ($k =~ /^(inventory|income|expense)_accno$/) {
      my $key = { inventory => 'IC_inventory', income => 'IC_income', expense => 'IC_expense' }->{$1};
      my ($d) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $x);
      $f->{$key} = $x ? "$x--" . ($d // '') : '';
    }
    elsif ($k eq 'partsgroup_id') {
      my ($g) = $dbh->selectrow_array("SELECT partsgroup FROM partsgroup WHERE id = ?", undef, $x);
      $f->{partsgroup} = $x ? "$g--$x" : '';
    }
    elsif ($k =~ /^(sellprice|listprice|lastcost|weight|rop)$/) { $f->{$k} = fmt($x) }
    else { $f->{$k} = $x }
  }
  $dbh->disconnect;
  # samengesteld artikel: onderdelen toevoegen zoals de browser (nummer in de lege regel + Bijwerken),
  # daarna aantal en BOM-vinkje per regel; [nummer, aantal, bom]
  if ($a->{components}) {
    for my $c (@{ $a->{components} }) {
      my $i = $f->{assembly_rows};
      $f->{"partnumber_$i"} = $c->[0];
      ($f) = $step->($f, 'update');
      die "onderdeel $c->[0] niet (eenduidig) gevonden\n" if $f->{assembly_rows} != $i + 1;
      $f->{"qty_$i"} = fmt($c->[1]);
      if ($c->[2]) { $f->{"bom_$i"} = 1 } else { delete $f->{"bom_$i"} }
    }
    ($f) = $step->($f, 'update');       # prijzen opnieuw optellen (assembly_row)
  }
  if ($a->{bom}) {                      # alleen vinkjes wijzigen: { regelnummer => 0/1 }
    for my $i (keys %{ $a->{bom} }) { if ($a->{bom}{$i}) { $f->{"bom_$i"} = 1 } else { delete $f->{"bom_$i"} } }
  }
  if ($mode eq 'new_number') {
    my ($g) = $step->($f, 'new_number');
    $res->{number} = $g->{partnumber};
    return;
  }
  my (undef, $r) = $step->($f, $mode eq 'save_as_new' ? 'save_as_new' : 'save');
  $res->{id} = $r->{id};
}
sub ic_stock {
  # ic.pl "Stock Assembly" als browser: list_assemblies (zonder magazijn/controle) -> aantal bij het
  # product invullen -> restock_assemblies. Negatief aantal = demonteren (kan in Perl ook zo).
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'ic';
  my $r = ic_request({ sort => 'partnumber' }, 'list_assemblies');
  die "$r->{error}\n" if $r->{error};
  my $f = html_fields($r->{html});
  delete $f->{callback};
  my $found;
  for my $i (1 .. $f->{rowcount}) {
    if ($f->{"id_$i"} == $a->{id}) { $f->{"qty_$i"} = fmt($a->{qty}); $found = 1 } else { $f->{"qty_$i"} = '' }
  }
  die "samengesteld artikel $a->{id} niet in de lijst\n" unless $found;
  $r = ic_request($f, 'restock_assemblies');
  die "$r->{error}\n" if $r->{error};
}
# ===== einde ic ==========================================================================


# ============================================================ blok "am": beheer (am.pl als browser)
# Rekeningschema, btw, instellingen, afsluiten, valuta, koersen, afdelingen, branches,
# betaalmethoden via de schermen van am.pl; talen (am.pl maakt sjabloonmappen) en projecten
# (PE.pm) via de bibliotheek. Python-tegenhanger: compare/compare_am.py (am_ui_py).
my @AM_LINKS = qw(AR AR_amount AR_tax AR_paid AR_discount AP AP_amount AP_tax AP_paid AP_discount
                  IC IC_income IC_sale IC_expense IC_cogs IC_taxpart IC_taxservice);

sub am_request {
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'am.pl';
    $form->{login} = $cfg->{login};
    $form->{admin} = 1;
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    # in CGI eindigt het request bij redirect (exec van de callback); zonder callback roept am.pl
    # daarna soms nog $form->error aan (save_currency, delete_currency)
    { no warnings; *Form::redirect = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/am.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
  });
}

sub am_ui {
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'am';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = am_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    my $f = html_fields($r->{html});
    delete $f->{callback};       # redirect doet anders exec
    return ($f, $r);
  };
  my $setflag = sub { my ($f, $k, $v) = @_; if ($v) { $f->{$k} = $v eq '1' ? 1 : $v } else { delete $f->{$k} } };
  my $do = $a->{do};
  if (defined $a->{id} && $a->{id} =~ /^(accno|desc|number):(.*)$/) {     # id opzoeken
    my ($k, $v) = ($1, $2);
    my $sql = $k eq 'accno' ? "SELECT id FROM chart WHERE accno = ?"
            : $k eq 'number' ? "SELECT id FROM project WHERE projectnumber = ?"
            : "SELECT id FROM $do WHERE description = ?";
    my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
    ($a->{id}) = $dbh->selectrow_array($sql, undef, $v);
    $dbh->disconnect;
  }
  if ($do eq 'account') {
    my ($f) = $a->{id} ? $step->({ id => $a->{id} }, 'edit_account') : $step->({}, 'add_account');
    for (qw(accno description category charttype gifi_accno)) { $f->{$_} = $a->{$_} if defined $a->{$_} }
    for (qw(contra closed)) { $setflag->($f, $_, $a->{$_}) if exists $a->{$_} }
    if ($a->{link}) {
      delete $f->{$_} for @AM_LINKS;
      $f->{$_} = $_ for @{ $a->{link} };
    }
    $step->($f, $a->{mode} || 'save');
  }
  elsif ($do eq 'taxes') {
    my ($f) = $step->({}, 'taxes');
    my $apply = sub {
      my ($f, $sets) = @_;
      for my $s (@{ $sets || [] }) {
        my $n = 0;
        for my $item (split / /, $f->{taxaccounts}) {
          my (undef, $i) = split /_/, $item;
          next unless $f->{"accno_$i"} eq $s->{accno};
          next unless ++$n == $s->{n};
          $f->{"taxrate_$i"} = fmt($s->{rate}) if defined $s->{rate};
          $f->{"taxnumber_$i"} = $s->{taxnumber} if defined $s->{taxnumber};
          $f->{"validto_$i"} = $s->{validto} if defined $s->{validto};
        }
      }
    };
    $apply->($f, $a->{set});
    for my $accno (keys %{ $a->{closed} || {} }) {
      for my $item (split / /, $f->{taxaccounts}) {
        my ($id, $i) = split /_/, $item;
        $setflag->($f, "closed_$id", $a->{closed}{$accno} ? 1 : 0) if $f->{"accno_$i"} eq $accno;
      }
    }
    if ($a->{add}) {
      ($f) = $step->($f, 'update');
      $apply->($f, $a->{add});
    }
    $step->($f, 'save');
  }
  elsif ($do eq 'defaults') {
    my ($f) = $step->({}, 'defaults');
    for my $k (keys %{ $a->{set} || {} }) {
      my $v = $a->{set}{$k};
      if ($k eq 'fxgainloss') {
        my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
        my ($d) = $dbh->selectrow_array("SELECT description FROM chart WHERE accno = ?", undef, $v);
        $dbh->disconnect;
        $f->{fxgainloss} = "$v--$d";
      } elsif (defined $v && $v ne '') { $f->{$k} = $v } else { delete $f->{$k} }
    }
    $step->($f, 'save');
  }
  elsif ($do eq 'closing') {
    my ($f) = $step->({}, 'audit_control');
    for (qw(closedto revtrans audittrail)) {
      next unless exists $a->{$_};
      my $v = $a->{$_};
      if (defined $v && $v ne '' && $v ne '0') { $f->{$_} = $v } else { delete $f->{$_} }
    }
    $step->($f, 'doclose');
  }
  elsif ($do eq 'currency') {
    my $mode = $a->{mode};
    if ($mode eq 'add') {
      my ($f) = $step->({}, 'add_currency');
      $f->{curr} = $a->{curr};
      $f->{prec} = $a->{prec};
      $step->($f, 'save');
    } elsif ($mode eq 'move') {
      $step->({ db => 'curr', fld => 'curr', id => $a->{curr}, move => $a->{move} }, 'move');
    } else {
      my ($f) = $step->({ curr => $a->{curr} }, 'edit_currency');
      $f->{prec} = $a->{prec} if defined $a->{prec};
      $step->($f, $mode eq 'delete' ? 'delete' : 'save');
    }
  }
  elsif ($do eq 'exchangerate') {
    my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
    my @c = map { s/\s+$//r } @{ $dbh->selectcol_arrayref("SELECT curr FROM curr ORDER BY rn") };
    $dbh->disconnect;
    shift @c;
    my $currs = join ':', @c;
    my ($f) = $a->{date} ? $step->({ transdate => $a->{date}, currencies => $currs }, 'edit_exchangerate')
                         : $step->({ currencies => $currs }, 'add_exchange_rate');
    $f->{transdate} = $a->{transdate} if defined $a->{transdate};
    for (keys %{ $a->{rates} || {} }) { $f->{"${_}exchangerate"} = fmt($a->{rates}{$_}) }
    $step->($f, 'save');
  }
  elsif ($do =~ /^(department|business|paymentmethod)$/) {
    my $t = $1;
    my $mode = $a->{mode} || 'save';
    if ($mode eq 'move') {
      $step->({ db => $t, fld => 'id', id => $a->{id}, move => $a->{move} }, 'move');
      return;
    }
    my %add = (department => 'add_department', business => 'add_business', paymentmethod => 'add_payment_method');
    my ($f) = $a->{id} ? $step->({ id => $a->{id} }, "edit_$t") : $step->({}, $add{$t});
    for (keys %{ $a->{set} || {} }) { $f->{$_} = $a->{set}{$_} }
    $step->($f, $mode);
  }
  elsif ($do eq 'language') {
    my $form = new_form();
    if (($a->{mode} || '') eq 'delete') {
      $form->{code} = $a->{code};
      AM->delete_language(\%myconfig, $form);
      return;
    }
    $form->{code} = $a->{code};
    $form->{description} = $a->{description};
    $form->{id} = $a->{oldcode} if $a->{oldcode};
    die "Error: Code missing!\n" unless $form->{code} =~ /\S/;
    die "Error: Description missing!\n" unless $form->{description} =~ /\S/;
    $form->{code} =~ s/(\.\.|\*)//g;               # am.pl save_language
    AM->save_language(\%myconfig, $form);
  }
  elsif ($do eq 'project') {
    require SL::PE;
    my $form = new_form();
    if (($a->{mode} || '') eq 'delete') {
      $form->{id} = $a->{id};
      PE->delete_project(\%myconfig, $form);
      return;
    }
    for (qw(id projectnumber description startdate enddate customer_id)) { $form->{$_} = $a->{$_} if defined $a->{$_} }
    PE->save_project(\%myconfig, $form);
  }
  else { die "onbekend am_ui: $do\n" }
}
# ============================================================ einde blok "am"

sub unescape_sel { my $v = shift // ''; $v =~ s/%([0-9a-fA-F]{2})/chr(hex($1))/eg; $v =~ s/\+/ /g; $v }

# ======================================================================= recurring (begin)
# Terugkerende boekingen als browser: herhaalschema op een gl/ar/ap-boeking
# (gl.pl/ar.pl/ap.pl edit -> schedule -> save_schedule/delete_schedule, arap.pl) en
# am.pl recurring_transactions -> process_transactions.

sub rec_request {
  # één request op een scherm in een eigen proces; exit en redirect (exec) worden opgevangen
  my ($script, $fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = $script;
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; *Form::redirect = sub { die "__REDIRECT__\n" }; }
    { package main; require "bin/mozilla/$script"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && ($err eq "__EXIT__\n" || $err eq "__REDIRECT__\n");
    die $err unless $ok;
    $res->{html} = $html;
  });
}

sub recurring_ui {
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  my $mode = $a->{mode} || 'schedule';
  if ($mode eq 'process') {
    $main::locale = new Locale '', 'am';
    my $r = rec_request('am.pl', {}, 'recurring_transactions');
    die "$r->{error}\n" if $r->{error};
    my $html = $r->{html};
    my $f = html_fields($html);
    my %want = map { $_ => 1 } @{ $a->{ids} || [] };
    my @screen;
    while ($html =~ /<input name="ndx_(\d+)" class=checkbox type=checkbox value=(\d+)\s*(checked)?/g) {
      my ($k, $id, $checked) = ($1, $2, $3);
      my ($next) = $html =~ /name="nextdate_$k" size=11 value="([^"]*)"/;
      push @screen, { id => $id, checked => $checked ? 1 : 0, nextdate => $next };
      if ($want{$id}) { $f->{"ndx_$k"} = $id } else { delete $f->{"ndx_$k"} }
      $f->{"nextdate_$k"} = $a->{nextdates}{$id} if $a->{nextdates} && $a->{nextdates}{$id};
    }
    $res->{screen} = \@screen;
    $f->{path} = 'bin/mozilla';
    my $p = rec_request('am.pl', $f, 'process_transactions');
    die "$p->{error}\n" if $p->{error};
    $res->{log} = $p->{html};
    return;
  }
  # fase 3 (R1): ook facturen met artikelen (is/ir) en orders (oe)
  my $script = { gl => 'gl.pl', ar => 'ar.pl', ap => 'ap.pl', is => 'is.pl', ir => 'ir.pl', oe => 'oe.pl' }->{$a->{module}}
    or die "module?\n";
  $main::locale = new Locale '', ({ gl => 'gl', ar => 'aa', ap => 'aa', is => 'is', ir => 'ir', oe => 'oe' }->{$a->{module}});
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = rec_request($script, $fields, $action);
    die "$r->{error}\n" if $r->{error};
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my %edit = (id => $a->{id});
  $edit{type} = 'invoice' if $a->{module} =~ /^i[sr]$/;
  $edit{type} = $a->{type} || 'sales_order' if $a->{module} eq 'oe';
  my ($f) = $step->(\%edit, 'edit');
  my ($g) = $step->($f, 'schedule');
  if ($mode eq 'delete') {
    $step->($g, 'delete_schedule');
    return;
  }
  for (qw(reference description startdate repeat unit howmany nextdate)) {
    $g->{"recurring$_"} = $a->{$_} if defined $a->{$_};
  }
  if ($a->{payment}) { $g->{recurringpayment} = 1 } else { delete $g->{recurringpayment} }
  $step->($g, 'save_schedule');
}
# ======================================================================= recurring (einde)

# ============================================================ blok "jc": tijdkaarten (jc.pl als browser, fase Uren U-1)
sub jc_request {
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'jc.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    # na opslaan zet jc.pl een callback en doet Form::redirect een exec van het script: hier gewoon stoppen
    { no warnings 'redefine'; *Form::redirect = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/jc.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{id} = $form->{id};
  });
}

sub jc_ui {
  # tijdkaart (type timecard, project): add/edit -> velden -> update (dienst ophalen, uren uit de tijden) ->
  # eigen aantal/prijs -> save (bij wijzigen met resave = "Continue"); delete -> yes
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'jc';
  my $step = sub {
    my ($fields, $action) = @_;
    my $r = jc_request($fields, $action);
    die "$r->{error}\n" if $r->{error};
    my $f = html_fields($r->{html});
    delete $f->{callback};
    return ($f, $r);
  };
  my %base = (type => 'timecard', project => 'project');
  my ($f) = $a->{id} ? $step->({ %base, id => $a->{id} }, 'edit') : $step->({ %base }, 'add');
  if (($a->{mode} || '') eq 'delete') {
    $step->({ %$f, %base, id => $a->{id} }, 'yes');
    return;
  }
  my %v = %{ $a->{fields} || {} };
  my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoInactiveDestroy => 1 });
  if (defined $v{projectnumber}) {
    my ($pid) = $dbh->selectrow_array("SELECT id FROM project WHERE projectnumber = ?", undef, $v{projectnumber});
    $f->{projectnumber} = "$v{projectnumber}--$pid";
  }
  if (defined $v{employee}) {
    my ($eid) = $dbh->selectrow_array("SELECT id FROM employee WHERE name = ?", undef, $v{employee});
    $f->{employee} = "$v{employee}--$eid";
  }
  $dbh->disconnect;
  for my $k (qw(transdate partnumber description notes inhour inmin outhour outmin noncharge)) {
    $f->{$k} = $v{$k} if defined $v{$k};
  }
  $f->{noncharge} = fmt($v{noncharge}) if defined $v{noncharge};
  ($f) = $step->({ %$f, %base }, 'update');
  $f->{qty} = fmt($v{qty}) if defined $v{qty};
  $f->{sellprice} = fmt($v{sellprice}) if defined $v{sellprice};
  ($f) = $step->({ %$f, %base }, 'update') if defined $v{qty};
  my (undef, $r) = $step->({ %$f, %base, ($a->{id} ? (resave => 1) : ()) }, 'save');
  $res->{id} = $r->{id};
}

sub pe_request {
  my ($fields, $action) = @_;
  in_child(sub {
    my $res = shift;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'pe.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { no warnings 'redefine'; *Form::redirect = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/pe.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
  });
}

sub pe_so {
  # pe.pl: Verkooporders uit project: project_jcitems_list (open uren, eventueel samengevat) -> generate_sales_orders
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'pe';
  my $r = pe_request({ vc => 'customer', type => 'sales_order', summary => ($a->{summary} ? 1 : ''),
                       transdatefrom => $a->{datefrom} // '', transdateto => $a->{dateto} // '' },
                     'project_jcitems_list');
  die "$r->{error}\n" if $r->{error};
  my $f = html_fields($r->{html});
  delete $f->{callback};
  if ($a->{rows}) {
    my %keep = map { $_ => 1 } @{ $a->{rows} };
    for my $i (1 .. $f->{rowcount}) { delete $f->{"checked_$i"} unless $keep{$i} }
  }
  my $g = pe_request({ %$f }, 'generate_sales_orders');
  die "$g->{error}\n" if $g->{error};
}
# ===== einde jc ==========================================================================

# ============================================================ blok "im": import (im.pl als browser, fase 4 stap 7)
my %IM_IMPORT = (customer => 'import_customers', vendor => 'import_vendors', part => 'import_parts',
                 service => 'import_services', partsgroup => 'import_groups', coa => 'import_chart_of_accounts',
                 gl => 'import_general_ledger', sales_invoice => 'import_sales_invoices',
                 sales_order => 'import_orders', purchase_order => 'import_orders', payment => 'import_payments');
my %IM_DOCS = map { $_ => 1 } qw(sales_invoice sales_order purchase_order payment);

sub im_ui {
  # im.pl: bestand (CSV, ';', strings quoted) -> im_<type> (klaarzetten in reportvars) -> rijen aanvinken -> import_*
  my ($a, $res) = @_;
  %main::myconfig = %myconfig;
  $main::locale = new Locale '', 'im';
  my $dir = "/tmp/sl_im_$$";
  mkdir $dir;
  $main::userspath = $dir;
  my $type = $a->{type};
  open my $fh, '>:encoding(UTF-8)', "$dir/import.csv" or die "$dir: $!";
  print $fh $a->{csv};
  close $fh;
  my $run = sub {
    my ($fields, $action) = @_;
    my $form = new_form();
    for (keys %$fields) { $form->{$_} = $fields->{$_} }
    $form->{path} = 'bin/mozilla';
    $form->{script} = 'im.pl';
    $form->{login} = $cfg->{login};
    $main::form = $form;
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/im.pl"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    my $ok = eval { &{"main::$action"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    return $form;
  };
  my %extra = %{ $a->{extra} || {} };
  my $f = $run->({ type => $type, tmpfile => 'import.csv', filename => 'import.csv', delimiter => ';',
                   stringsquoted => 1, filetype => 'csv', %extra }, "im_$type");
  my %keep = (type => $type, rowcount => $f->{rowcount}, login => $cfg->{login}, path => 'bin/mozilla');
  if ($IM_DOCS{$type}) {
    # documenten/betalingen: im.pl zet alles als verborgen velden op het scherm; neem ze allemaal mee
    for (keys %$f) { $keep{$_} = $f->{$_} if !ref $f->{$_} && !exists $keep{$_} }
    %keep = (%keep, %extra);
  }
  my %chosen = map { $_ => 1 } @{ $a->{rows} || [1 .. $f->{rowcount}] };
  for my $i (1 .. $f->{rowcount}) { $keep{"ndx_$i"} = $chosen{$i} ? 1 : '' }
  if ($type eq 'gl') { for my $i (1 .. $f->{rowcount}) { $keep{"reference_$i"} = $f->{"reference_$i"} } }
  my $g = $run->(\%keep, $IM_IMPORT{$type});
  $res->{rowcount} = $f->{rowcount};
  $res->{added} = $g->{added} // '';
  $res->{updated} = $g->{updated} // '';
  $res->{failed} = $g->{failed} // '';
  unlink "$dir/import.csv";
  rmdir $dir;
}
# ===== einde im ==========================================================================

sub in_child {
  my ($code) = @_;
  my $pid = open(my $kid, '-|') // die "fork: $!";
  if ($pid) {
    my $json = do { local $/; <$kid> };
    close $kid;
    return decode_json($json || '{"error":"geen uitvoer (exit)"}');
  }
  my $child = {};
  eval { $code->($child); 1 } or $child->{error} = "$@";
  $child->{error} =~ s/\s+$// if $child->{error};
  print JSON::PP->new->canonical->allow_blessed->allow_unknown->encode($child);
  exit 0;
}

for my $op (@{ $scenario->{ops} }) {
  my $res = { op => $op->{op} };
  eval {
    if ($op->{op} eq 'post') {
      my $t = $op->{trans};
      my $form = new_form();
      $form->{id} = resolve($t->{id}) if $t->{id};
      $form->{transdate} = $t->{transdate};
      for (qw(reference description notes)) { $form->{$_} = $t->{$_} if defined $t->{$_} }
      $form->{department} = "x--$t->{department_id}" if $t->{department_id};
      $form->{defaultcurrency} = defaultcurrency();
      $form->{currency} = $t->{currency} if $t->{currency};
      $form->{exchangerate} = fmt($t->{exchangerate}) if defined $t->{exchangerate};
      $form->{pending} = 1 if $t->{pending};
      $form->{batchid} = $t->{batch_id} if $t->{batch_id};
      my $i = 0;
      for my $l (@{ $t->{lines} }) {
        $i++;
        $form->{"accno_$i"} = "$l->{accno}--x";
        $form->{"debit_$i"} = fmt($l->{debit});
        $form->{"credit_$i"} = fmt($l->{credit});
        $form->{"source_$i"} = $l->{source} // '';
        $form->{"memo_$i"} = $l->{memo} // '';
        $form->{"projectnumber_$i"} = "x--$l->{project_id}" if $l->{project_id};
        $form->{"cleared_$i"} = $l->{cleared} // '';
        $form->{"fx_transaction_$i"} = $l->{fx_transaction} ? 1 : 0;
      }
      $form->{rowcount} = $i;
      if (my $r = $t->{recurring}) {
        my $email = join ':', map { @$_ } @{ $r->{email} || [] };
        my $print = join ':', map { ($_->[0], $_->[1], $_->[2] // '') } @{ $r->{print} || [] };
        $form->{recurring} = join ',', map { $_ // '' }
          ($r->{reference}, $r->{description}, $r->{startdate}, $r->{repeat}, $r->{unit},
           $r->{howmany}, ($r->{payment} ? 1 : 0), $print, $email, $r->{message});
        $form->{recurringnextdate} = $r->{nextdate} if $r->{nextdate};
      }
      my $n = 0;
      for my $ref (@{ $t->{references} || [] }) {
        $n++;
        $form->{"referencedescription_$n"} = $ref->{description};
        $form->{"referencecode_$n"} = $ref->{code};
        $form->{"referencearchive_id_$n"} = $ref->{archive_id};
        $form->{"referencefolder_$n"} = $ref->{folder};
        $form->{"referenceconfidential_$n"} = $ref->{confidential} ? 1 : '';
      }
      $form->{reference_rows} = $n;
      $form->{referenceurl} = 1;    # geen bestandsuploads

      check_post($form, $t);
      GL->post_transaction(\%myconfig, $form) or die "Error: Cannot post transaction!\n";
      $saved{$op->{save_as}} = $form->{id} if $op->{save_as};
      $res->{id} = $form->{id};
    }
    elsif ($op->{op} eq 'delete') {
      my $form = new_form();
      $form->{id} = resolve($op->{id});
      $form->{batchid} = $op->{batch_id} if $op->{batch_id};
      my $dbh = $form->dbconnect(\%myconfig);
      ($form->{reference}) = $dbh->selectrow_array("SELECT reference FROM gl WHERE id = $form->{id}");
      my %d = $form->get_defaults($dbh, \@{[qw(closedto revtrans precision)]});
      my ($old) = $dbh->selectrow_array("SELECT transdate FROM gl WHERE id = $form->{id}");
      $dbh->disconnect;
      $form->{precision} = $d{precision};
      die "Error: locked\n" if $d{revtrans} || $form->datetonum(\%myconfig, $old) <= $form->datetonum(\%myconfig, $d{closedto});
      GL->delete_transaction(\%myconfig, $form) or die "Error: Cannot delete transaction!\n";
    }
    elsif ($op->{op} eq 'get') {
      my $form = new_form();
      $form->{id} = resolve($op->{id});
      GL->transaction(\%myconfig, $form);
      $res->{header} = { map { $_ => $form->{$_} } qw(id reference description notes transdate department_id department curr exchangerate approved batchid batchdescription employee_id) };
      $res->{fxadj} = $form->{fxadj} ? 1 : 0;
      $res->{lines} = $form->{GL} || [];
      $res->{recurring} = $form->{recurring};
      $res->{references} = $form->{all_reference} || [];
      $res->{readonly} = $form->{readonly} ? 1 : 0;
    }
    elsif ($op->{op} eq 'list') {
      my $form = new_form();
      my $s = $op->{search} || {};
      $form->{category} = 'X';
      for (keys %$s) { $form->{$_} = $s->{$_} }
      $form->{department} = "x--$s->{department_id}" if $s->{department_id};
      $form->{l_lineitem} = 1 if $s->{include_lineitem};
      $form->{l_splitledger} = 1 if $s->{splitledger};
      $form->{amountfrom} = fmt($s->{amountfrom}) if $s->{amountfrom};
      $form->{amountto} = fmt($s->{amountto}) if $s->{amountto};
      # in CGI is elke aanroep een nieuw proces; package-globals van GL resetten
      %GL::seen = (); %GL::balance = ();
      GL->transactions(\%myconfig, $form);
      $res->{rows} = $form->{GL} || [];
      $res->{balance} = $form->{balance};
      $res->{datefrom} = $form->{datefrom};
      $res->{dateto} = $form->{dateto};
    }
    elsif ($op->{op} eq 'accounts') {
      my $form = new_form();
      CA->all_accounts(\%myconfig, $form);
      $res->{rows} = $form->{CA} || [];
    }
    elsif ($op->{op} eq 'account_tx') {
      my $form = new_form();
      my $a = $op->{args} || {};
      for (keys %$a) { $form->{$_} = $a->{$_} }
      $form->{accounttype} = 'gifi' if $a->{gifi_accno} && !$a->{accno};
      $form->{department} = "x--$a->{department_id}" if $a->{department_id};
      $form->{projectnumber} = "x--$a->{project_id}" if $a->{project_id};
      CA->all_transactions(\%myconfig, $form);
      $res->{rows} = $form->{CA} || [];
      $res->{balance} = $form->{balance};
    }
    elsif ($op->{op} =~ /^(trial_balance|income_statement|balance_sheet)$/) {
      my $report = $1;
      # elk rapport in een eigen proces, zoals een CGI-request (rp.pl gebruikt globals)
      my $pid = open(my $kid, '-|') // die "fork: $!";
      if ($pid) {
        my $json = do { local $/; <$kid> };
        close $kid;
        my $r = decode_json($json);
        %$res = (%$res, %$r);
        die $r->{error} . "\n" if $r->{error};
        1;
      } else {
        my $child = {};
        eval { run_report($report, $op, $child); 1 } or $child->{error} = "$@";
        print JSON::PP->new->canonical->allow_blessed->allow_unknown->encode($child);
        exit 0;
      }
    }
    elsif ($op->{op} =~ /^(account_screen|gl_screen)$/) {
      my $pid = open(my $kid, '-|') // die "fork: $!";
      if ($pid) {
        my $json = do { local $/; <$kid> };
        close $kid;
        my $r = decode_json($json);
        %$res = (%$res, %$r);
        die $r->{error} . "\n" if $r->{error};
        1;
      } else {
        my $child = {};
        my $run = ($op->{op} eq 'gl_screen') ? \&run_gl_screen : \&run_account_screen;
        eval { $run->($op, $child); 1 } or $child->{error} = "$@";
        print JSON::PP->new->canonical->allow_blessed->allow_unknown->encode($child);
        exit 0;
      }
    }
    elsif ($op->{op} =~ /^(aging|reminder|tax_report|payments)$/) {
      my $report = $1;
      my $pid = open(my $kid, '-|') // die "fork: $!";
      if ($pid) {
        my $json = do { local $/; <$kid> };
        close $kid;
        my $r = decode_json($json);
        %$res = (%$res, %$r);
        die $r->{error} . "\n" if $r->{error};
        1;
      } else {
        my $child = {};
        eval { run_arap_report($report, $op, $child); 1 } or $child->{error} = "$@";
        print JSON::PP->new->canonical->allow_blessed->allow_unknown->encode($child);
        exit 0;
      }
    }
    elsif ($op->{op} eq 'yearend') {
      # am.pl generate_yearend + AM::post_yearend
      my $a = $op->{args};
      my $form = new_form();
      $form->{todate} = $a->{todate};
      $form->{accno} = $a->{accno};
      $form->{reference} = $a->{reference} if $a->{reference};
      $form->{description} = $a->{description};
      $form->{method} = $a->{method} || 'accrual';
      my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
      ($form->{precision}) = $dbh->selectrow_array("SELECT fldvalue FROM defaults WHERE fldname = 'precision'");
      $dbh->disconnect;
      RP->yearend_statement(\%myconfig, $form);
      $form->{transdate} = $form->{todate};
      my ($earnings, $ok) = (0, 0);
      $form->{rowcount} = 1;
      for (keys %{ $form->{I} }) {
        if ($form->{I}{$_}{charttype} eq "A") {
          $form->{"debit_$form->{rowcount}"} = $form->{I}{$_}{amount};
          $earnings += $form->{I}{$_}{amount};
          $form->{"accno_$form->{rowcount}"} = $_;
          $form->{rowcount}++; $ok = 1;
        }
      }
      for (keys %{ $form->{E} }) {
        if ($form->{E}{$_}{charttype} eq "A") {
          $form->{"credit_$form->{rowcount}"} = $form->{E}{$_}{amount} * -1;
          $earnings += $form->{E}{$_}{amount};
          $form->{"accno_$form->{rowcount}"} = $_;
          $form->{rowcount}++; $ok = 1;
        }
      }
      if ($earnings > 0) {
        $form->{"credit_$form->{rowcount}"} = $earnings;
      } else {
        $form->{"debit_$form->{rowcount}"} = $earnings * -1;
      }
      $form->{"accno_$form->{rowcount}"} = $form->{accno};
      die "Error: Nothing to do!\n" unless $ok && $earnings;
      AM->post_yearend(\%myconfig, $form) or die "Error: Yearend posting failed!\n";
      $res->{id} = $form->{id};
      $res->{earnings} = $earnings;
    }
    elsif ($op->{op} eq 'save_level') {
      my $form = new_form();
      my @ids;
      for my $id (keys %{ $op->{levels} }) {
        my $tid = resolve($id);
        push @ids, $tid;
        $form->{"ndx_$tid"} = 1;
        $form->{"level_$tid"} = $op->{levels}{$id};
      }
      $form->{ids} = join ' ', @ids;
      RP->save_level(\%myconfig, $form) or die "Error: Could not save reminder level!\n";
    }
    elsif ($op->{op} eq 'aa_ui') {
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { aa_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as} && $r->{id};
    }
    elsif ($op->{op} eq 'gl_ui') {
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { gl_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as};
    }
    elsif ($op->{op} eq 'is_ui') {      # is/ir (facturen met artikelen, fase 3)
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { is_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as} && $r->{id};
    }
    elsif ($op->{op} eq 'print_ui') {   # documenten (fase 4): sjabloonvariabelen van is/ir/oe print
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { print_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'rp_print_ui') {   # aanmaningen/rekeningoverzichten (fase 4 stap 4a)
      my $a = { %{ $op->{args} || {} } };
      my $r = in_child(sub { rp_print_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'oe_ui') {      # oe (orders en offertes, fase 3)
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { oe_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as} && $r->{id};
    }
    elsif ($op->{op} eq 'pe_so') {      # pe (verkooporders uit project, fase Uren U-2)
      my $a = { %{ $op->{args} || {} } };
      my $r = in_child(sub { pe_so($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'jc_ui') {      # jc (tijdkaarten, fase Uren)
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { jc_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as} && $r->{id};
    }
    elsif ($op->{op} eq 'im_ui') {      # im (import, fase 4 stap 7)
      my $a = { %{ $op->{args} || {} } };
      my $r = in_child(sub { im_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'ic_ui') {      # ic (artikelen, fase 3)
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { ic_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as} && $r->{id};
    }
    elsif ($op->{op} eq 'ic_stock') {   # ic: assembleren (fase 3, samengestelde artikelen)
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id});
      my $r = in_child(sub { ic_stock($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'ct_ui') {      # ct (relaties, fase 2)
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { ct_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as} && $r->{id};
    }
    elsif ($op->{op} =~ /^arap_(post|repost|get)$/) {
      my $mode = $1 eq 'post' ? 'new' : $1;
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      my $r = in_child(sub { arap_ui($mode, $a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
      $saved{$op->{save_as}} = $r->{id} if $op->{save_as};
    }
    elsif ($op->{op} eq 'arap_repost_all') {
      my $a = $op->{args};
      my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
      my $ids = $dbh->selectcol_arrayref($a->{sql});
      $dbh->disconnect;
      # schermcode één keer laden; de kindprocessen erven die
      $main::form = arap_form($a->{vc});
      %main::myconfig = %myconfig;
      { package main; require "bin/mozilla/arap.pl"; require "bin/mozilla/arapprn.pl"; require "bin/mozilla/aa.pl"; }
      my %err;
      for my $id (@$ids) {
        my $r = in_child(sub { arap_ui('repost', { vc => $a->{vc}, id => $id }, $_[0]) });
        $err{$id} = $r->{error} if $r->{error};
      }
      $res->{count} = scalar @$ids;
      $res->{errors} = \%err;
    }
    elsif ($op->{op} eq 'cp_payments') {
      cp_payments_ui({ %{ $op->{args} || {} } }, $res);
    }
    elsif ($op->{op} eq 'cp_post') {
      my $a = { %{ $op->{args} || {} } };
      cp_ui($a, $res);
    }
    elsif ($op->{op} eq 'cp_pay_all') {
      # alle openstaande posten per relatie en valuta afbetalen via het betaalscherm
      my $a = $op->{args};
      my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
      my $groups = $dbh->selectall_arrayref($a->{sql});
      $dbh->disconnect;
      my %err;
      for my $g (@$groups) {
        my ($vc_id, $curr, $ids) = @$g;
        my %r;
        my $args = { %$a, vc_id => $vc_id, currency => $curr,
                     exchangerate => $a->{exchangerates}{$curr}, rows => [ map { { id => $_ } } split /,/, $ids ] };
        eval { cp_ui($args, \%r); 1 } or $err{"$vc_id/$curr"} = "$@";
        $err{"$vc_id/$curr"} =~ s/\s+$// if $err{"$vc_id/$curr"};
      }
      $res->{count} = scalar @$groups;
      $res->{errors} = \%err;
    }
    elsif ($op->{op} =~ /^cp_(void|reissue)$/) {
      # betalingsregister: aangevinkte cheque/ontvangst storneren of opnieuw uitgeven
      my $what = $1;
      my $a = $op->{args};
      my $r = in_child(sub {
        my $res = shift;
        my $form = new_form();
        $form->{vc} = $a->{vc};
        $form->{ARAP} = ($a->{vc} eq 'customer') ? 'AR' : 'AP';
        my $arap = lc $form->{ARAP};
        my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
        my $ids = $dbh->selectcol_arrayref(qq|SELECT DISTINCT ac.trans_id FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                                               JOIN $arap a ON a.id = ac.trans_id
                                               WHERE c.accno = ? AND ac.source = ? ORDER BY 1|, undef, $a->{accno}, $a->{source});
        my ($vc_id) = $dbh->selectrow_array("SELECT $a->{vc}_id FROM $arap WHERE id = ?", undef, $ids->[0]);
        $dbh->disconnect;
        $form->{rowcount} = 1;
        $form->{id_1} = join "\n", @$ids;
        $form->{accno_1} = $a->{accno};
        $form->{source_1} = $a->{source};
        $form->{"$a->{vc}_id_1"} = $vc_id;
        if ($what eq 'void') {
          CP->void_payments(\%myconfig, $form) or die "Error: Could not void payments!\n";
        } else {
          $form->{source} = $a->{new_source};
          CP->reissue_payment(\%myconfig, $form, 1) or die "Error: Error processing payment!\n";
        }
      });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'arap_list') {
      # aa.pl transactions (AA::transactions + scherm): ruwe rijen en HTML
      my $a = $op->{args} || {};
      my $r = in_child(sub {
        my $res = shift;
        my $form = arap_form($a->{vc});
        for (keys %$a) { $form->{$_} = $a->{$_} unless ref $a->{$_} }
        $form->{$form->{vc}} = $a->{name} if $a->{name};
        $form->{"$form->{vc}number"} = $a->{vcnumber} if $a->{vcnumber};
        $form->{"$form->{vc}_id"} = $a->{vc_id} if $a->{vc_id};
        $form->{department} = "$a->{department}--x" if $a->{department};
        $form->{$form->{ARAP}} = "$a->{arap_accno}--x" if $a->{arap_accno};
        for (qw(summary outstanding open closed paidlate paidearly onhold)) { $form->{$_} = $a->{$_} ? 1 : '' }
        $form->{l_subtotal} = $a->{l_subtotal} ? 'Y' : '';
        for (qw(id transdate invnumber name netamount tax amount paid due datepaid duedate)) { $form->{"l_$_"} = 'Y' }
        # l_memo / l_curr zijn in aa.pl tegelijk kolom én detail resp. valuta-uitsplitsing
        $form->{l_memo} = $a->{l_memo} ? 'Y' : '';
        $form->{l_curr} = $a->{l_curr} ? 'Y' : '';
        $main::form = $form;
        %main::myconfig = %myconfig;
        $main::locale = new Locale '', 'aa';
        { package main; require "bin/mozilla/arap.pl"; require "bin/mozilla/aa.pl"; }
        my $html = '';
        open my $out, '>', \$html;
        my $old = select $out;
        my $ok = eval { main::transactions(); 1 };
        my $err = $@;
        select $old;
        die $err unless $ok;
        $res->{rows} = [ map { { %$_, vc_id => $_->{"$a->{vc}_id"} } } @{ $form->{transactions} || [] } ];
        $res->{summary} = $form->{summary} ? 1 : 0;
        $res->{sort} = $a->{sort};
        $res->{html} = $html;
      });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'names') {
      # Form::get_name (zoeken) en Form::all_vc (keuzelijst)
      my $a = $op->{args};
      my $form = new_form();
      my $vc = $a->{vc};
      if ($a->{list}) {
        $form->{"${vc}_id"} = $a->{include_id} if $a->{include_id};
        my %cfg = (%myconfig, vclimit => 1000000);
        $form->all_vc(\%cfg, $vc, ($vc eq 'customer' ? 'AR' : 'AP'), undef, $a->{transdate}, undef, $a->{open_invoices});
        $res->{rows} = $form->{"all_$vc"} || [];
      } else {
        $form->{$vc} = $a->{name} // '';
        $form->{"${vc}number"} = $a->{number} // '';
        $form->{searchby} = $a->{number} ? "${vc}number" : 'name';     # arap.pl check_name
        $form->get_name(\%myconfig, $vc, $a->{transdate});
        $res->{rows} = $form->{name_list} || [];
        my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '');
        my ($nbn) = $dbh->selectrow_array("SELECT fldvalue FROM defaults WHERE fldname = 'namesbynumber'");
        $dbh->disconnect;
        $res->{by} = ($a->{number} || $nbn) ? "${vc}number" : 'name';
      }
    }
    elsif ($op->{op} eq 'arap_delete') {
      my $form = arap_form($op->{vc});
      $form->{id} = resolve($op->{id});
      my $table = $op->{vc} eq 'customer' ? 'ar' : 'ap';
      my $dbh = $form->dbconnect(\%myconfig);
      ($form->{invnumber}, my $old) = $dbh->selectrow_array("SELECT invnumber, transdate FROM $table WHERE id = $form->{id}");
      my %d = $form->get_defaults($dbh, \@{[qw(closedto revtrans)]});
      $dbh->disconnect;
      die "Error: locked\n" if $d{revtrans} || $form->datetonum(\%myconfig, $old) <= $form->datetonum(\%myconfig, $d{closedto});
      AA->delete_transaction(\%myconfig, $form) or die "Error: Cannot delete transaction!\n";
    }
    # ---------------------------------------------------------------- recurring (begin)
    elsif ($op->{op} eq 'recurring_ui') {
      my $a = { %{ $op->{args} || {} } };
      $a->{id} = resolve($a->{id}) if $a->{id};
      $a->{ids} = [ map { resolve($_) } @{ $a->{ids} || [] } ];
      my $r = in_child(sub { recurring_ui($a, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    # ---------------------------------------------------------------- recurring (einde)
    # ---- rc (afletteren, fase 2)
    elsif ($op->{op} eq 'rc_screen') {
      my $a = $op->{args} || {};
      $res->{html} = rc_first($a);
    }
    elsif ($op->{op} eq 'rc_ui') {
      my $r = in_child(sub { rc_ui({ %{ $op->{args} || {} } }, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    # ---- rc einde
    elsif ($op->{op} eq 'am_ui') {        # blok "am"
      my $r = in_child(sub { am_ui({ %{ $op->{args} || {} } }, $_[0]) });
      %$res = (%$res, %$r);
      die "$r->{error}\n" if $r->{error};
    }
    elsif ($op->{op} eq 'ref_save') {     # stap 4e: bestand bij een boeking zoals aa.pl/rd.pl (Form::save_reference, tmpfile)
      my $a = $op->{args};
      my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '', { AutoCommit => 0 }) or die;
      my $form = new_form();
      $form->{login} = $a->{login} if $a->{login};
      $form->{userspath} = $a->{dir};
      $form->{id} = resolve($a->{trans_id});
      $form->all_references($dbh);       # bestaande verwijzingen gaan mee zoals de verborgen velden van het scherm
      my $i = 0;
      for my $ref (@{ $form->{all_reference} || [] }) {
        $i++;
        for (qw(code description archive_id folder)) { $form->{"reference${_}_$i"} = $ref->{$_} }
        $form->{"referenceconfidential_$i"} = $ref->{confidential};
      }
      $i++;
      $form->{"referencedescription_$i"} = $a->{description} // '';
      $form->{"referencetmpfile_$i"} = $a->{tmpfile};
      $form->{"referencefilename_$i"} = $a->{filename};
      $form->{"referenceconfidential_$i"} = $a->{confidential} ? 1 : '';
      $form->{reference_rows} = $i;
      $form->save_reference($dbh, $a->{formname});
      $dbh->commit;
      ($res->{archive_id}) = $dbh->selectrow_array("SELECT max(archive_id) FROM reference WHERE trans_id = ?", undef, $form->{id});
      $dbh->disconnect;
    }
    elsif ($op->{op} eq 'ref_get') {      # stap 4e: Form::get_reference (rd.pl display_documents) + all_references
      my $form = new_form();
      $form->{login} = $op->{args}{login} if $op->{args}{login};
      $form->{id} = resolve($op->{args}{archive_id});
      my $data = $form->get_reference(\%myconfig);
      $res->{hex} = unpack 'H*', ($data // '');
      $res->{filename} = $form->{filename};
      $res->{contenttype} = $form->{contenttype};
      $res->{description} = $form->{description};
      if (my $tid = resolve($op->{args}{trans_id})) {
        my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '') or die;
        my $f2 = new_form();
        $f2->{login} = $op->{args}{login} if $op->{args}{login};
        $f2->{id} = $tid;
        $f2->all_references($dbh);
        $res->{references} = [ map { { description => $_->{description}, archive_id => $_->{archive_id},
                                        filename => $_->{filename}, formname => $_->{formname},
                                        confidential => $_->{confidential} ? 1 : 0 } } @{ $f2->{all_reference} || [] } ];
        $dbh->disconnect;
      }
    }
    elsif ($op->{op} eq 'sql') {
      my $dbh = DBI->connect($myconfig{dbconnect}, 'postgres', '') or die;
      $dbh->do($op->{sql}) or die "Error: $DBI::errstr\n";
      $dbh->disconnect;
    }
    elsif ($op->{op} eq 'amounts') {
      my $form = new_form();
      $res->{parse} = [ map { $form->parse_amount(\%myconfig, $_) } @{ $op->{parse} || [] } ];
      $res->{format} = [ map { $form->format_amount(\%myconfig, @$_) } @{ $op->{format} || [] } ];
      $res->{round} = [ map { $form->round_amount(@$_) } @{ $op->{round} || [] } ];
    }
    1;
  } or do {
    my $err = $@ || 'unknown error';
    $err =~ s/\s+$//;
    $res->{error} = $err;
  };
  push @results, $res;
}

print JSON::PP->new->canonical->allow_blessed->allow_unknown->encode(\@results);
