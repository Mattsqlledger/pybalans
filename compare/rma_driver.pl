#!/usr/bin/perl
# Vult een Run my Accounts-administratie via RMA's EIGEN schermcode (bin/mozilla/*.pl), zoals een
# browser dat doet: scherm openen -> velden invullen -> knoppen (update/save/post/...). Zo schrijft RMA
# zelf zijn tabellen (o.a. inventory vanuit facturen, trf, build). Alleen voor de synthetische testset.
#
# gebruik: PGHOST=.. PGPORT=.. PGUSER=.. perl rma_driver.pl <rma-dir> <dbnaam> <scenario.json>
#   RMA_DEBUG=<map>  schrijft de html van elke stap naar die map
#
# Scenario: { "config": {...}, "ops": [ ... ] } met ops:
#   {"op":"sql", "sql":"..."}                       eigen SQL (alleen voor wat RMA niet via een scherm kan)
#   {"op":"screen", "script":"ct.pl", "start":"add", "fields":{"db":"customer"},
#    "steps":[ {"set":{...}, "lines":[{...}], "do":"save"} ], "keep":{"naam":"id"}}
# Waarden: "$naam" = eerder bewaarde waarde; "#accno" = "accno--omschrijving"; "~tabel:kolom=waarde" = id.
# Regels ("lines"): partnumber in de lege regel + update, daarna de overige velden van die regel.
use strict;
no strict 'refs';
use warnings;
no warnings;
use JSON::PP;
use DBI;
use File::Spec;
use File::Temp qw(tempfile);
binmode STDOUT, ':encoding(UTF-8)';

my $SELF = File::Spec->rel2abs($0);
if (($ARGV[0] // q{}) eq q{--child}) { child_request(@ARGV[1, 2]); exit 0 }
my ($rmadir, $dbname, $scenario_file) = @ARGV;
die "gebruik: rma_driver.pl <rma-dir> <db> <scenario.json>\n" unless $scenario_file;
open my $fh, '<', $scenario_file or die "$scenario_file: $!";
my $scenario = JSON::PP->new->utf8->relaxed->decode(do { local $/; <$fh> });
close $fh;
@ARGV = ();
unshift @INC, $rmadir;
chdir $rmadir or die "$rmadir: $!";
require SL::Form;

my $cfg = $scenario->{config} || {};
my %myconfig = (
  dbconnect    => "dbi:Pg:dbname=$dbname",
  dbname       => $dbname,
  dbuser       => $ENV{PGUSER} || 'postgres',
  dbpasswd     => '',
  dbdriver     => 'Pg',
  dboptions    => "set DateStyle to 'ISO'",
  numberformat => '1000.00',
  dateformat   => 'yyyy-mm-dd',
  countrycode  => '',
  name         => $cfg->{name} || 'Test',
  email        => 'test@example.invalid',
  login        => $cfg->{login} || 'test',
  timeout      => 86400,
  vclimit      => 0,
  role         => 'admin',
);

my %saved;
my $stepno = 0;

sub dbh { DBI->connect($myconfig{dbconnect}, $myconfig{dbuser}, '', { AutoInactiveDestroy => 1, RaiseError => 1 }) }

sub resolve {
  my $v = shift;
  return $v unless defined $v && !ref $v;
  if ($v =~ /^\$(\w+)$/) { die "onbekende waarde \$$1\n" unless exists $saved{$1}; return $saved{$1} }
  if ($v =~ /^#(\S+)$/) {
    my $d = dbh(); my ($desc) = $d->selectrow_array('SELECT description FROM chart WHERE accno = ?', undef, $1); $d->disconnect;
    die "rekening $1 bestaat niet\n" unless defined $desc;
    return "$1--$desc";
  }
  if ($v =~ /^~~(\w+):(\w+)=(.*)$/) {    # "waarde--id" zoals in keuzelijsten
    my $d = dbh(); my ($id) = $d->selectrow_array("SELECT id FROM $1 WHERE $2 = ?", undef, $3); $d->disconnect;
    die "$1.$2 = $3 niet gevonden\n" unless defined $id;
    return "$3--$id";
  }
  if ($v =~ /^~(\w+):(\w+)=(.*)$/) {
    my $d = dbh(); my ($id) = $d->selectrow_array("SELECT id FROM $1 WHERE $2 = ?", undef, $3); $d->disconnect;
    die "$1.$2 = $3 niet gevonden\n" unless defined $id;
    return $id;
  }
  $v =~ s/\$\{(\w+)\}/$saved{$1}/g;
  $v;
}

sub request {    # één request op een scherm, in een eigen, vers gestart proces (zoals CGI)
  my ($script, $fields, $action) = @_;
  my ($tfh, $tfile) = tempfile('rmareqXXXXXX', TMPDIR => 1, UNLINK => 1);
  print $tfh JSON::PP->new->utf8->encode({ script => $script, action => $action, fields => $fields, myconfig => \%myconfig });
  close $tfh;
  open(my $kid, '-|', $^X, $SELF, '--child', $rmadir, $tfile) // die "start: $!";
  my $json = do { local $/; <$kid> };
  close $kid;
  unlink $tfile;
  return JSON::PP->new->utf8->decode($json || qq|{"error":"geen uitvoer (status $?)"}|);
}

sub child_request {
  my ($dir, $file) = @_;
  open my $in, '<', $file or die "$file: $!";
  my $req = JSON::PP->new->utf8->decode(do { local $/; <$in> });
  close $in;
  chdir $dir or die "$dir: $!";
  unshift @INC, $dir;
  require SL::Form;
  binmode STDOUT;
  my $res = {};
  my $real = \*STDOUT;
  eval {
    my $script = $req->{script};
    my $form = bless {}, 'Form';
    $form->{$_} = $req->{fields}{$_} for keys %{ $req->{fields} };
    $form->{path} = 'bin/mozilla';
    $form->{script} = $script;
    $form->{login} = $req->{myconfig}{login};
    $form->{version} = '2.8.33';
    $form->{action} = $req->{action};
    (my $s = $script) =~ s/\.pl$//;
    %main::myconfig = %{ $req->{myconfig} };
    $main::locale = new Locale '', $s;
    $main::form = $form;
    $main::userspath = 'users'; $main::spool = 'spool'; $main::templates = 'templates'; $main::images = 'images';
    $main::memberfile = 'users/members';
    { no warnings; *CORE::GLOBAL::exit = sub { die "__EXIT__\n" }; }
    { package main; require "bin/mozilla/$script"; }
    # zoals am.pl e.a.: daarna custom_<scherm>.pl (RMA overschrijft daar subs, o.a. invoicetax/fifo)
    if (-f "bin/mozilla/custom_$script") { package main; require "bin/mozilla/custom_$script"; }
    my $html = '';
    open my $out, '>', \$html;
    my $old = select $out;
    require Carp;
    local $SIG{__DIE__} = sub { die $_[0] . ($_[0] eq "__EXIT__\n" ? q{} : Carp::longmess(q{})) } if $ENV{RMA_TRACE};
    my $ok = eval { &{"main::$req->{action}"}(); 1 };
    my $err = $@;
    select $old;
    $ok = 1 if !$ok && $err eq "__EXIT__\n";
    die $err unless $ok;
    $res->{html} = $html;
    $res->{$_} = $form->{$_} for grep { !ref $form->{$_} } keys %$form;
    1;
  } or $res->{error} = "$@";
  $res->{error} =~ s/\s+$// if $res->{error};
  print {$real} JSON::PP->new->utf8->canonical->allow_blessed->allow_unknown->encode($res);
}

sub html_fields {    # velden die een browser bij het versturen van dit scherm zou meesturen
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
      $v =~ s/^\s+|\s+$//g;    # browser stuurt optietekst zonder witruimte
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

sub step {
  my ($script, $fields, $action) = @_;
  my $r = request($script, $fields, $action);
  $stepno++;
  if (my $dir = $ENV{RMA_DEBUG}) {
    open my $d, '>:raw', sprintf('%s/%03d-%s-%s.html', $dir, $stepno, $script, $action);
    print $d ($r->{html} // ''), "\n<!-- error: ", ($r->{error} // ''), " -->\n";
    close $d;
  }
  die "$script $action: $r->{error}\n" if $r->{error};
  # een foutmelding van RMA zelf ($form->error) komt als html terug
  if (($r->{html} // '') =~ /<body[^>]*>\s*<h2 class=(?:"|)error(?:"|)>(.*?)<\/h2>/is) {
    (my $m = $1) =~ s/<[^>]+>//g;
    die "$script $action: RMA-fout: $m\n";
  }
  my $f = html_fields($r->{html} // '');
  delete $f->{callback};
  return ($f, $r);
}

sub set_fields {
  my ($f, $set) = @_;
  for my $k (keys %{ $set || {} }) {
    my $v = $set->{$k};
    if (!defined $v || (!ref $v && $v eq '-')) { delete $f->{$k}; next }    # vinkje uit / veld weg
    $f->{$k} = resolve($v);
  }
}

sub screen {
  my ($op) = @_;
  my $script = $op->{script};
  my %start = %{ $op->{fields} || {} };
  $start{$_} = resolve($start{$_}) for keys %start;
  my ($f, $r) = step($script, \%start, $op->{start} || 'add');
  my $always = sub {    # scenario-brede vaste waarden (bv. afdeling) in lege velden die het scherm heeft
    for my $k (keys %{ $scenario->{always} || {} }) {
      $f->{$k} = resolve($scenario->{always}{$k}) if exists $f->{$k} && ($f->{$k} // q{}) eq q{};
    }
  };
  $always->();
  for my $s (@{ $op->{steps} || [] }) {
    $script = $s->{script} if $s->{script};    # bv. oe.pl -> knop factuur -> verder op is.pl
    set_fields($f, $s->{set});
    for my $l (@{ $s->{lines} || [] }) {
      my $rf = $s->{rows} || q{rowcount};    # ic.pl-assemblage: assembly_rows
      my $i = $f->{$rf} || 1;
      $f->{"partnumber_$i"} = $l->{partnumber};
      ($f, $r) = step($script, $f, q{update});
      $always->();
      die "$script: artikel $l->{partnumber} niet (eenduidig) gevonden\n" if ($f->{$rf} || 0) != $i + 1;
      for (grep { $_ ne 'partnumber' } keys %$l) { $f->{"${_}_$i"} = resolve($l->{$_}) }
    }
    set_fields($f, $s->{after});
    $always->();
    ($f, $r) = step($script, $f, $s->{do}) if $s->{do};
  }
  if ($op->{show}) { print STDERR "  $_ = $f->{$_}\n" for sort keys %$f }
  for my $k (keys %{ $op->{keep} || {} }) {
    my $src = $op->{keep}{$k};
    $saved{$k} = $r->{$src} // $f->{$src};
    die "$script: '$src' niet gevonden om als \$$k te bewaren\n" unless defined $saved{$k} && $saved{$k} ne '';
  }
  return { id => $r->{id} };
}

my @log;
my $n = 0;
for my $op (@{ $scenario->{ops} }) {
  $n++;
  my $label = $op->{label} || "$op->{op} $n";
  if ($op->{op} eq 'sql') {
    my $d = dbh();
    $d->do(resolve($_)) for (ref $op->{sql} ? @{ $op->{sql} } : ($op->{sql}));
    $d->disconnect;
  } elsif ($op->{op} eq 'screen') {
    my $res = eval { screen($op) };
    if (!$res) { print STDERR "FOUT bij [$label]: $@"; exit 1 }
  } else {
    die "onbekende op $op->{op}\n";
  }
  push @log, $label;
  print STDERR "ok  $label\n" if $ENV{RMA_VERBOSE};
}
print JSON::PP->new->canonical->pretty->encode({ ok => scalar(@log), saved => \%saved });
