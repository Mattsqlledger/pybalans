#!/usr/bin/perl
# Werk een SQL-Ledger-dataset bij naar de databaseversie van de opgegeven
# sql-ledger-map, met de originele User::dbupdate (zoals bij inloggen).
# gebruik: PGHOST=.. PGPORT=.. PGUSER=.. perl upgrade_dataset.pl <sql-ledger-dir> <dbnaam>
use DBI;
my ($sldir, $db) = @ARGV;
chdir $sldir; unshift @INC, $sldir;
require SL::Form; require SL::User;
my $form = bless {}, 'Form';
%$form = (dbdriver => 'Pg', dbname => $db, dbuser => $ENV{PGUSER} || 'postgres', dbpasswd => '',
          dateformat => 'yyyy-mm-dd', dbversion => '3.2.4');
(bless {}, 'User')->dbupdate($form);
print "dataset $db bijgewerkt\n";
