/**
 * Collection endpoint for the Mix-n-match picture comparison study.
 *
 * Setup, once:
 *   1. Create a Google Sheet. Rename the first tab to "responses".
 *   2. Extensions > Apps Script. Delete the placeholder and paste this file.
 *   3. Deploy > New deployment > type "Web app".
 *        Execute as:      Me
 *        Who has access:  Anyone
 *      Authorise when prompted (the "unverified app" warning is your own script).
 *   4. Copy the /exec URL and paste it into js/config.js as `endpoint`.
 *
 * After changing this file you must Deploy > Manage deployments > edit > New
 * version, or the live URL keeps serving the old code.
 *
 * One submission becomes one row in "responses" plus one row per answer in
 * "answers". The long form is what analysis/analyze.py reads; the wide row is
 * there so you can eyeball progress without leaving the Sheet.
 */

var SUMMARY_SHEET = 'responses';
var ANSWER_SHEET = 'answers';

var SUMMARY_HEADERS = [
  'received_at', 'participant', 'study', 'manifest_version', 'manifest_built_at',
  'started_at', 'finished_at', 'duration_ms', 'answers', 'familiarity', 'device',
  'screen', 'viewport', 'user_agent', 'raw_json'
];

var ANSWER_HEADERS = [
  'received_at', 'participant', 'item_id', 'set', 'config', 'seed',
  'num_crops', 'tiles_per_crop', 'combinations',
  'a_method', 'b_method',
  'overall', 'seamless', 'coherence', 'alignment',
  'win_overall', 'win_seamless', 'win_coherence', 'win_alignment',
  'position', 'ms', 'answered_at'
];


function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, service: 'mix-n-match study' }))
    .setMimeType(ContentService.MimeType.JSON);
}


function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return reply({ ok: false, error: 'empty request' });
    }
    var data = JSON.parse(e.postData.contents);
    if (!data.participant || !data.responses || !data.responses.length) {
      return reply({ ok: false, error: 'no answers in submission' });
    }

    /* One submission at a time, so two participants finishing together cannot
       write over each other's row. */
    var lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      writeSummary(data);
      writeAnswers(data);
    } finally {
      lock.releaseLock();
    }
    return reply({ ok: true, answers: data.responses.length });
  } catch (error) {
    return reply({ ok: false, error: String(error) });
  }
}


function reply(body) {
  return ContentService
    .createTextOutput(JSON.stringify(body))
    .setMimeType(ContentService.MimeType.JSON);
}


function sheetFor(name, headers) {
  var book = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = book.getSheetByName(name);
  if (!sheet) {
    sheet = book.insertSheet(name);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(headers);
    sheet.setFrozenRows(1);
  }
  return sheet;
}


function writeSummary(data) {
  var background = data.background || {};
  sheetFor(SUMMARY_SHEET, SUMMARY_HEADERS).appendRow([
    new Date(),
    data.participant,
    data.study || '',
    data.manifest_version || '',
    data.manifest_built_at || '',
    data.started_at || '',
    data.finished_at || '',
    data.duration_ms || '',
    data.responses.length,
    background.familiarity || '',
    background.device || '',
    data.screen || '',
    data.viewport || '',
    data.user_agent || '',
    JSON.stringify(data)
  ]);
}


function writeAnswers(data) {
  var now = new Date();
  var rows = data.responses.map(function (answer) {
    var picks = answer.answers || {};
    var wins = answer.winners || {};
    return [
      now,
      data.participant,
      answer.id || '',
      answer.set || '',
      answer.config || '',
      valueOr(answer.seed),
      valueOr(answer.num_crops),
      valueOr(answer.tiles_per_crop),
      /* One tile index per crop joined by '|', one combination per image joined by ';'. */
      (answer.combinations || []).map(function (c) { return c.join('|'); }).join(';'),
      answer.a_method || '',
      answer.b_method || '',
      picks.overall || '',
      picks.seamless || '',
      picks.coherence || '',
      picks.alignment || '',
      wins.overall || '',
      wins.seamless || '',
      wins.coherence || '',
      wins.alignment || '',
      valueOr(answer.position),
      valueOr(answer.ms),
      answer.at || ''
    ];
  });
  if (!rows.length) return;
  var sheet = sheetFor(ANSWER_SHEET, ANSWER_HEADERS);
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, ANSWER_HEADERS.length).setValues(rows);
}


/** Keep a real 0 rather than letting it fall through to an empty cell. */
function valueOr(value) {
  return (value === undefined || value === null) ? '' : value;
}
