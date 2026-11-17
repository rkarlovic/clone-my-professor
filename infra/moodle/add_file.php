<?php
// Dev only: add a file to a course of the local Moodle as a File resource (to test the
// material sync without Moodle's file picker).
//
//   php add_file.php <course shortname> <path inside the container> [hidden]
//
// "hidden" adds it hidden from students (CMP must not take it).

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/course/modlib.php');

[$script, $shortname, $path] = array_pad($argv, 3, null);
$hidden = ($argv[3] ?? '') === 'hidden';
if (!$shortname || !$path || !is_readable($path)) {
    fwrite(STDERR, "usage: php add_file.php <course shortname> <path> [hidden]\n");
    exit(1);
}
$admin = get_admin();
\core\session\manager::set_user($admin);
$course = $DB->get_record('course', ['shortname' => $shortname], '*', MUST_EXIST);
$name = basename($path);

$draft = file_get_unused_draft_itemid();
get_file_storage()->create_file_from_pathname([
    'contextid' => context_user::instance($admin->id)->id,
    'component' => 'user',
    'filearea' => 'draft',
    'itemid' => $draft,
    'filepath' => '/',
    'filename' => $name,
], $path);

$info = create_module((object)[
    'modulename' => 'resource',
    'course' => $course->id,
    'section' => 1,
    'visible' => $hidden ? 0 : 1,
    'name' => pathinfo($name, PATHINFO_FILENAME),
    'introeditor' => ['text' => '', 'format' => FORMAT_HTML, 'itemid' => 0],
    'files' => $draft,
    'display' => 0,
]);
echo "added $name as module {$info->coursemodule}" . ($hidden ? ' (hidden)' : '') . "\n";
