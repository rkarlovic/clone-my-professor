<?php
// Dev only: register CMP as an LTI 1.3 tool in the local Moodle and create a test course with
// a teacher, a student and a CMP activity. Safe to run again (reuses what exists).
//
//   docker compose -f infra/docker-compose.dev.yml --env-file .env --profile moodle \
//       exec -u www-data moodle php /opt/cmp/setup_cmp.php
//
// Prints the values CMP needs (scripts.lti_platform add ...). Names and e-mails are never sent
// to CMP (rule 9): the tool is configured with sendname/sendemailaddr = never.

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->dirroot . '/mod/lti/locallib.php');
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/course/modlib.php');
require_once($CFG->dirroot . '/user/lib.php');

\core\session\manager::set_user(get_admin());

$cmp = rtrim(getenv('CMP_PUBLIC_URL') ?: 'http://127.0.0.1:5173', '/');
$password = getenv('MOODLE_TEST_PASSWORD');
$toolname = 'CMP (dev)';

// 1. The tool type (site level), LTI 1.3 with CMP's public key set.
$type = $DB->get_record('lti_types', ['name' => $toolname]);
if (!$type) {
    $t = new stdClass();
    $t->state = LTI_TOOL_STATE_CONFIGURED;
    $t->name = $toolname;
    $t->description = 'Clone My Professor: AI asistent iz nastavnikovih materijala';
    $config = new stdClass();
    $config->lti_typename = $toolname;
    $config->lti_toolurl = "$cmp/api/lti/launch";
    $config->lti_ltiversion = LTI_VERSION_1P3;
    $config->lti_keytype = LTI_JWK_KEYSET;
    $config->lti_publickeyset = "$cmp/api/lti/jwks";
    $config->lti_initiatelogin = "$cmp/api/lti/login";
    $config->lti_redirectionuris = "$cmp/api/lti/launch";
    $config->lti_coursevisible = LTI_COURSEVISIBLE_ACTIVITYCHOOSER;
    $config->lti_launchcontainer = LTI_LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW;
    $config->lti_sendname = LTI_SETTING_NEVER;        // rule 9: no names
    $config->lti_sendemailaddr = LTI_SETTING_NEVER;   // rule 9: no e-mails
    $config->lti_acceptgrades = LTI_SETTING_NEVER;    // rule 7: CMP does not grade
    $config->lti_forcessl = 0;
    $config->lti_contentitem = 0;
    $typeid = lti_add_type($t, $config);
    $type = $DB->get_record('lti_types', ['id' => $typeid], '*', MUST_EXIST);
}

// 2. Test users (passwords from .env).
function cmp_user(string $username, string $first, string $last, string $password): int {
    global $DB, $CFG;
    if ($u = $DB->get_record('user', ['username' => $username])) {
        return (int)$u->id;
    }
    return (int)user_create_user((object)[
        'username' => $username,
        'password' => $password,
        'firstname' => $first,
        'lastname' => $last,
        'email' => "$username@moodle.test",
        'auth' => 'manual',
        'confirmed' => 1,
        'mnethostid' => $CFG->mnet_localhost_id,
    ], true);
}
$teacherid = cmp_user('teacher1', 'Test', 'Nastavnik', $password);
$studentid = cmp_user('student1', 'Test', 'Student', $password);

// 3. A course with both enrolled.
$course = $DB->get_record('course', ['shortname' => 'DAROV-DEMO']);
if (!$course) {
    $course = create_course((object)[
        'fullname' => 'Rad s darovitom djecom (Moodle demo)',
        'shortname' => 'DAROV-DEMO',
        'category' => 1,
    ]);
}
$manual = enrol_get_plugin('manual');
$instance = $DB->get_record('enrol', ['courseid' => $course->id, 'enrol' => 'manual'], '*', MUST_EXIST);
$manual->enrol_user($instance, $teacherid, $DB->get_field('role', 'id', ['shortname' => 'editingteacher']));
$manual->enrol_user($instance, $studentid, $DB->get_field('role', 'id', ['shortname' => 'student']));

// 4. The CMP activity in the course.
$cmid = null;
foreach (get_coursemodules_in_course('lti', $course->id) as $existing) {
    if ($existing->name === 'Asistent (CMP)') {
        $cmid = $existing->id;
    }
}
if (!$cmid) {
    $info = create_module((object)[
        'modulename' => 'lti',
        'course' => $course->id,
        'section' => 0,
        'visible' => 1,
        'name' => 'Asistent (CMP)',
        'introeditor' => ['text' => '', 'format' => FORMAT_HTML, 'itemid' => 0],
        'typeid' => $type->id,
        'toolurl' => '',
        'launchcontainer' => LTI_LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW,
        'instructorchoicesendname' => 0,
        'instructorchoicesendemailaddr' => 0,
        'instructorchoiceacceptgrades' => 0,
        'grade' => 0,
    ]);
    $cmid = $info->coursemodule;
}

// 5. Material sync (Web Services): a dedicated user that may only read course contents and
//    download files, through a service with one function. Least privilege: no editing rights,
//    no access to hidden sections (CMP takes only what students can see).
require_once($CFG->dirroot . '/webservice/lib.php');
set_config('enablewebservices', 1);
$protocols = array_filter(explode(',', get_config('core', 'webserviceprotocols') ?: ''));
if (!in_array('rest', $protocols)) {
    $protocols[] = 'rest';
    set_config('webserviceprotocols', implode(',', $protocols));
}
$syncid = cmp_user('cmp_sync', 'CMP', 'Sync', $password . 'Ws');
$roleid = $DB->get_field('role', 'id', ['shortname' => 'cmpsync']);
if (!$roleid) {
    $roleid = create_role('CMP sync', 'cmpsync', 'Reads course contents for Clone My Professor');
    set_role_contextlevels($roleid, [CONTEXT_SYSTEM]);
}
$system = context_system::instance();
foreach (['webservice/rest:use', 'moodle/course:view', 'moodle/course:viewparticipants'] as $cap) {
    assign_capability($cap, CAP_ALLOW, $roleid, $system->id, true);
}
role_assign($roleid, $syncid, $system->id);
$ws = new webservice();
$service = $DB->get_record('external_services', ['shortname' => 'cmp_sync']);
if (!$service) {
    $serviceid = $ws->add_external_service((object)[
        'name' => 'CMP sync',
        'shortname' => 'cmp_sync',
        'enabled' => 1,
        'restrictedusers' => 1,
        'downloadfiles' => 1,
        'uploadfiles' => 0,
    ]);
    $ws->add_external_function_to_service('core_course_get_contents', $serviceid);
    $service = $DB->get_record('external_services', ['id' => $serviceid], '*', MUST_EXIST);
}
if (!$DB->record_exists('external_services_users', ['externalserviceid' => $service->id, 'userid' => $syncid])) {
    $ws->add_ws_authorised_user((object)['externalserviceid' => $service->id, 'userid' => $syncid]);
}
$token = $DB->get_field('external_tokens', 'token', ['externalserviceid' => $service->id, 'userid' => $syncid]);
if (!$token) {
    $token = \core_external\util::generate_token(EXTERNAL_TOKEN_PERMANENT, $service, $syncid, $system);
}

echo json_encode([
    'ws_token' => $token,  // a secret: the caller writes it to .env and does not print it
    'issuer' => $CFG->wwwroot,
    'client_id' => $type->clientid,
    'deployment_id' => (string)$type->id,
    'auth_url' => $CFG->wwwroot . '/mod/lti/auth.php',
    'jwks_url' => $CFG->wwwroot . '/mod/lti/certs.php',
    'course_id' => (int)$course->id,
    'activity_url' => $CFG->wwwroot . '/mod/lti/view.php?id=' . $cmid,
], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n";
