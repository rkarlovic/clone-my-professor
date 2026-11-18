<?php
// Dev-only Moodle configuration (build step 4); values come from the container environment
// (.env), never from this file.
unset($CFG);
global $CFG;
$CFG = new stdClass();

$CFG->dbtype    = 'pgsql';
$CFG->dblibrary = 'native';
$CFG->dbhost    = 'moodle-db';
$CFG->dbname    = 'moodle';
$CFG->dbuser    = 'moodle';
$CFG->dbpass    = getenv('MOODLE_DB_PASSWORD');
$CFG->prefix    = 'mdl_';
$CFG->dboptions = ['dbpersist' => false, 'dbport' => 5432];

$CFG->wwwroot   = getenv('MOODLE_WWWROOT') ?: 'http://127.0.0.1:8080';
// Test setup inside Docker: CMP's containers reach this Moodle as http://<MOODLE_INTERNAL_HOST>
// (its key set and Web Services; no browser involved). Moodle redirects every other host name to
// wwwroot, so requests under that name keep it. Browsers always use the wwwroot above.
$internal = getenv('MOODLE_INTERNAL_HOST');
if (PHP_SAPI !== 'cli' && $internal && ($_SERVER['HTTP_HOST'] ?? '') === $internal) {
    $CFG->wwwroot = 'http://' . $internal;
}
$CFG->dataroot  = '/var/www/moodledata';
$CFG->admin     = 'admin';
$CFG->directorypermissions = 02777;

require_once(__DIR__ . '/lib/setup.php');
