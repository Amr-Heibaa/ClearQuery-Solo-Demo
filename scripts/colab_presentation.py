"""Readable notebook output only; never repairs or substitutes model output."""
from html import escape


def text(value):
    if value is None:
        return '—'
    if isinstance(value, (tuple, list)):
        return ', '.join(text(item) for item in value) or '—'
    if isinstance(value, dict):
        return '; '.join(f'{key}: {text(item)}' for key, item in value.items()) or '—'
    return str(value)


def table(columns, rows):
    head = ''.join('<th>'+escape(str(c))+'</th>' for c in columns)
    body = ''.join('<tr>'+''.join('<td>'+escape(text(c))+'</td>' for c in row)+'</tr>' for row in rows)
    return '<div style="overflow-x:auto"><table border="1" cellpadding="8"><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'


def exact_html(title, value, rtl=False):
    direction = 'rtl' if rtl else 'ltr'
    return '<h4>'+escape(title)+'</h4><pre dir="'+direction+'" style="white-space:pre-wrap;text-align:'+('right' if rtl else 'left')+'">'+escape(text(value))+'</pre>'


def result_html(result):
    outcome = result.get('stop_reason')
    if outcome == 'REVIEWED_SQL_EXECUTION_DISABLED':
        outcome = 'SQL approved — separate database authorization required'
    parts = [exact_html('Trace ID', result.get('trace_id')),
        table(['Pipeline outcome', 'Time (ms)'], [[outcome, result.get('runtime_ms')]])]
    for stage in result.get('stages', []):
        name, value = stage['stage'], stage.get('result')
        if value is None:
            if stage.get('error'): parts.append(exact_html(name+' error', stage['error']))
            continue
        if name in {'asr', 'confirmation', 'translation', 'sql_candidate'}:
            key = {'asr':'transcript_ar_raw','confirmation':'question_ar','translation':'text_en','sql_candidate':'candidate_sql'}[name]
            parts.append(exact_html({'asr':'Raw Whisper Arabic (unchanged)', 'confirmation':'Confirmed Arabic',
                'translation':'Exact English translation', 'sql_candidate':'Generated SQL (unchanged)'}[name], value.get(key), name in {'asr','confirmation'}))
            if name == 'sql_candidate' and value.get('raw_model_output') != value.get('candidate_sql'):
                parts.append('<details><summary>Exact raw XiYan output</summary>'+exact_html('',value.get('raw_model_output'))+'</details>')
        elif name == 'bir':
            parts.append('<h4>Business Intent Representation</h4>'+table(['Field','Value'], [(k,v) for k,v in value.items() if k not in {'trace_id','contract_version','question_ar'}]))
        elif name == 'decision':
            parts.append(exact_html('Decision', value['decision']))
            if value.get('clarification_question'): parts.append(exact_html('Please clarify / مطلوب توضيح',value['clarification_question'],True))
            if value.get('reason_code'): parts.append(exact_html('Reason',value['reason_code']))
        elif name in {'semantic_gate','sql_guard'}:
            verdict = value.get('status') if name == 'semantic_gate' else ('APPROVED' if value.get('validated') is True else 'REJECTED')
            parts.append(exact_html('Translation Gate' if name == 'semantic_gate' else 'SQL Guard',verdict))
            parts.append(table(['Check','Status'], [(c.get('field',c.get('name')),c['status']) for c in value.get('checks',[])]))
            if value.get('rejected_reason'):parts.append(exact_html('Rejection reason',value['rejected_reason']))
        else:
            parts.append('<details><summary>'+escape(name)+'</summary>'+table(['Field','Value'],value.items())+'</details>')
        if stage.get('error'):parts.append(exact_html('Stage error',stage['error']))
    return '\n'.join(parts)


def database_html(result):
    parts = [exact_html('Read-only synthetic database result', 'EXECUTED' if result.get('executed') and not result.get('error') else 'STOPPED')]
    if result.get('error'):parts.append(exact_html('Database error',result['error']))
    else:
        parts.append(table(result.get('columns',[]),result.get('rows',[])))
        parts.append(exact_html('Rows / time',f"{result.get('row_count',0)} rows · {result.get('elapsed_ms')} ms"))
        if result.get('truncated'):parts.append(exact_html('Limit','Results truncated at the configured row cap.'))
    return '\n'.join(parts)
