#!/usr/bin/perl
# Maakt een lege Run my Accounts-administratie zoals RMA's eigen admin.pl dat doet
# (User::dbcreate: Pg-tables, gifi, rekeningschema, indices, Pg-custom_tables) en werkt hem
# daarna bij met User::dbupdate (zoals bij het inloggen) tot de opgegeven versie.
#
# gebruik: PGHOST=.. PGPORT=.. PGUSER=.. perl rma_create.pl <rma-dir> <dbnaam> <chart> <versie> [zonder-custom]
#   chart:  bv. Swiss-German of Germany-SKR03
#   versie: bv. 2.8.52 of 2.8.26
# De scripts in db_updates/ worden bewust NIET gedraaid: update_2.8.4x-*.pl doen hetzelfde als de
# SQL-stappen, IBP-*/customer_vendor_notes_converter zijn dataschoonmaak, en alle lopen over
# elke database op een vaste server.
use strict;
use warnings;
use DBI;

my ($rmadir, $db, $chart, $version, $nocustom) = @ARGV;
die "gebruik: rma_create.pl <rma-dir> <db> <chart> <versie> [zonder-custom]\n" unless $version;
chdir $rmadir or die "$rmadir: $!";
unshift @INC, $rmadir;
require SL::Form;
require SL::User;

my $form = bless {}, 'Form';
%$form = (
  dbdriver   => 'Pg',
  dbuser     => $ENV{PGUSER} || 'postgres',
  dbpasswd   => '',
  dbdefault  => 'postgres',
  db         => $db,
  chart      => $chart,
  encoding   => 'UTF8',
  dateformat => 'yy-mm-dd',
);

if ($nocustom) {    # dbcreate laadt Pg-custom_*.sql alleen als het bestand bestaat
  rename 'sql/Pg-custom_tables.sql', 'sql/Pg-custom_tables.sql.uit' or die $!;
}
my $user = bless {}, 'User';
eval { $user->dbcreate($form) };
my $err = $@;
rename 'sql/Pg-custom_tables.sql.uit', 'sql/Pg-custom_tables.sql' if $nocustom;
die $err if $err;

%$form = (%$form, dbupdate => "db$db", "db$db" => 1);
my $dbh = DBI->connect("dbi:Pg:dbname=$db", $form->{dbuser}, '') or die $DBI::errstr;
if (User::calc_version($version) > User::calc_version('2.8.51')) {
  $form->{dbversion} = '2.8.51';
  $user->dbupdate($form);
  # Pg-upgrade-2.8.51-2.8.52.sql wijzigt xabschluss(_history), maar geen RMA-script maakt die tabellen
  # (bij RMA vermoedelijk door een externe dienst). Minimaal nagebootst, anders stopt de keten hier.
  $dbh->do($_) or die $dbh->errstr for (
    q|CREATE TABLE IF NOT EXISTS xabschluss (id serial PRIMARY KEY, xml text NOT NULL)|,
    q|CREATE TABLE IF NOT EXISTS xabschluss_history (id serial PRIMARY KEY, xml text NOT NULL)|,
    q|COMMENT ON TABLE xabschluss IS 'nagebootst door rma_create.pl (testset PyBalans)'|,
  );
}
$form->{dbversion} = $version;
$user->dbupdate($form);

my ($v) = $dbh->selectrow_array("SELECT fldvalue FROM defaults WHERE fldname = 'version'");
print "RMA-administratie $db aangemaakt, versie $v\n";
