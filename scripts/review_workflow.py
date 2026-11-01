#!/usr/bin/env python3
"""Local review checkpoints and immutable releases. No CAD or engineering approval."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import uuid

from validate_review import validate, ARTIFACT_FIELDS


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8', newline='\n') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists(): tmp.unlink()


@contextmanager
def lock(project):
    project = Path(project).resolve(); project.mkdir(parents=True, exist_ok=True)
    path = project / '.review-workflow.lock'
    try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError: raise ValueError('工作流正被使用或上次中断遗留锁；核实进程后再处理锁文件')
    try:
        os.write(fd, json.dumps(dict(pid=os.getpid(), time=now())).encode()); os.close(fd)
        yield project
    finally: path.unlink()


def within(project, value):
    p = (Path(project) / value).resolve()
    if not p.is_relative_to(Path(project).resolve()): raise ValueError('路径必须在项目输出目录内')
    return p


def load_state(project):
    project = Path(project).resolve(); pointer = project / 'workflow.json'
    if not pointer.exists(): return None
    ptr = read(pointer); path = within(project, ptr['state_file'])
    if digest(path) != ptr['state_sha256']: raise ValueError('状态快照哈希不一致')
    state = read(path)
    if state['revision'] != ptr['revision']: raise ValueError('状态版本与入口不一致')
    return state


def expect(state, revision):
    if (state or {}).get('revision') != revision: raise ValueError('状态已更新，请读取最新 revision 后重新提交')


def commit(project, state):
    """Only workflow.json is the commit point; failed writes retain the prior pointer."""
    state = dict(state, revision=uuid.uuid4().hex, updated_at=now())
    folder = project / 'checkpoints' / state['revision']; folder.mkdir(parents=True)
    write(folder / 'state.json', state)
    latest = state.get('latest_validated')
    lines = ['# 审图续审入口', '', '工作状态与工程审核结论分开保存。', '',
             '最新已验证成果：' + (latest['report'] if latest else '尚未登记'),
             '工作轮次：' + state.get('work_round', ''), '当前对象：' + state.get('current_object', ''), '',
             '## 已确认基线', *['- '+x for x in state.get('baseline_confirmations', [])], '',
             '## 工作步骤（不计入正式完成率）', *['- '+x for x in state.get('completed_steps', [])], '',
             '## 候选发现', *['- '+x for x in state.get('candidate_findings', [])], '',
             '## 下一步', *['- '+x for x in state.get('next_actions', [])], '',
             '## 工作产物', *['- '+x for x in state.get('work_artifacts', [])], '',
             '## 进程记录', *['- '+x for x in state.get('running_processes', [])], '',
             '状态版本：'+state['revision']]
    write(folder / 'resume.md', '\n'.join(lines))
    pointer = dict(schema_version='1.0', revision=state['revision'], state_file=str((folder/'state.json').relative_to(project)),
                   state_sha256=digest(folder/'state.json'), resume_file=str((folder/'resume.md').relative_to(project)))
    write(project / 'workflow.json', pointer)
    return dict(pointer, latest_validated=latest)


LIST_FIELDS = {'baseline_confirmations', 'completed_steps', 'candidate_findings', 'next_actions', 'running_processes', 'work_artifacts'}
TEXT_FIELDS = {'work_round', 'current_object'}


def checkpoint(project, changes, expected_revision):
    if not isinstance(changes, dict) or set(changes) - LIST_FIELDS - TEXT_FIELDS: raise ValueError('未知状态字段')
    for k,v in changes.items():
        if k in LIST_FIELDS and (not isinstance(v,list) or any(not isinstance(x,str) or not x.strip() for x in v)): raise ValueError(k+' 必须为非空字符串数组')
        if k in TEXT_FIELDS and not isinstance(v,str): raise ValueError(k+' 必须为字符串')
    with lock(project) as project:
        state = load_state(project); expect(state, expected_revision)
        for name in changes.get('work_artifacts', []):
            if not within(project, name).is_file(): raise ValueError('工作产物不存在: '+name)
        if changes.get('work_round') and not within(project, changes['work_round']).is_dir(): raise ValueError('工作轮次目录不存在')
        state = dict(state or {'schema_version':'1.0'}, **changes)
        return commit(project, state)


def artifacts(data, base):
    result = {}
    for row in data['evidence'] + data.get('relations', []):
        for key in ARTIFACT_FIELDS:
            if key in row:
                p = within(base, row[key])
                if not p.is_file(): raise ValueError('证据文件不存在: '+str(p))
                result[p.relative_to(base).as_posix()] = digest(p)
    return result


def status(project):
    state = load_state(project)
    if not state: return None
    latest = state.get('latest_validated'); warnings = []
    if latest:
        try:
            if digest(latest['review']) != latest['review_sha256']: warnings.append('已登记台账发生变化，不能继续视为同一已验证版本')
            if latest['kind'] == 'release':
                manifest_path = Path(latest['manifest']); manifest = read(manifest_path)
                if digest(manifest_path) != latest['manifest_sha256']: warnings.append('发布清单发生变化')
                for name, h in manifest['files'].items():
                    if digest(within(manifest_path.parent, name)) != h: warnings.append('发布文件发生变化: '+name)
            else:
                if digest(latest['legacy_qa']) != latest['legacy_qa_sha256']: warnings.append('历史复核记录发生变化')
                if digest(latest['report']) != latest['legacy_report_sha256']: warnings.append('历史报告发生变化')
        except (OSError, ValueError, KeyError): warnings.append('已验证成果或附件缺失/不可读')
    return dict(state, integrity_warnings=warnings)


def checked(review_path):
    p = Path(review_path).resolve(); raw=p.read_bytes(); sha=hashlib.sha256(raw).hexdigest(); data=json.loads(raw.decode('utf-8-sig'))
    validation = validate(data, True, p.parent)
    if not validation['valid']: raise ValueError(json.dumps(validation['errors'], ensure_ascii=False))
    if digest(p)!=sha:raise ValueError('校验期间台账发生变化')
    validation['review_sha256']=sha
    return p, data, validation


def prepare_qa(review_path, output):
    p, data, _ = checked(review_path)
    if Path(output).exists(): raise ValueError('复核记录已存在，拒绝覆盖')
    q = dict(review_sha256=digest(p), status='pending', reviewed_by='', evidence_ids_checked=[],
             limitations=[], artifact_hashes=artifacts(data,p.parent), note='填写实际图面复核结果后改为 reviewed；生成模板不代表复核通过。')
    write(output,q); return q


def verify_qa(q, data, sha, manifest):
    if not isinstance(q,dict) or q.get('review_sha256') != sha or q.get('status') != 'reviewed': raise ValueError('缺少与当前台账绑定的已完成图面复核')
    if not isinstance(q.get('reviewed_by'),str) or not q['reviewed_by'].strip(): raise ValueError('须记录实际复核者')
    if not isinstance(q.get('limitations'),list) or any(not isinstance(x,str) for x in q['limitations']): raise ValueError('须提供复核限制数组')
    if q.get('artifact_hashes') != manifest: raise ValueError('证据文件在复核后发生变化')
    ids = q.get('evidence_ids_checked')
    if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids): raise ValueError('缺少复核证据ID数组')
    ev = {e['evidence_id'] for e in data['evidence']}
    if len(ids)!=len(set(ids)) or not set(ids)<=ev: raise ValueError('复核证据ID重复或不存在')
    required = {eid for i in data['issues'] if i['severity']=='high' and i['judgement'] in {'confirmed_conflict','verified_noncompliance','confirmed_missing'} and i['status'] not in {'closed','duplicate'} for eid in i['evidence_ids']}
    if not required<=set(ids): raise ValueError('高影响确认问题证据未全部回查')
    passes = [c for c in data['checks'] if c['result']=='pass']
    if passes and not any(set(c['evidence_ids'])<=set(ids) for c in passes): raise ValueError('至少记录一个通过项的完整证据抽查')


def table(headers, rows):
    def cell(v): return str(v).replace('|','\\|').replace('\n',' / ')
    return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join('---' for _ in headers)+'|']+['|'+'|'.join(cell(v) for v in row)+'|' for row in rows])


def reports(folder, data, validation):
    m=validation['metrics']; total=m['applicable_or_unknown_checks']; done=m['completed_checks']
    ratio=f'{done/total:.2%}' if total else '无适用分母'
    summary=f"声明检查记录完成 {done}/{total}（{ratio}）。该比例不是工时进度、全套工作量进度或合格率；任务汇总不额外计数。"
    pages={
      'report': '# '+data['project']['name']+'\n\n'+data['project']['round']+'\n\n'+summary+'\n\n'+data['project'].get('baseline','')+'\n\n'+data['project'].get('delivery_state','阶段成果；按检查清单确认未完成范围。')+'\n\n'+table(['状态','数量'],list(m['results'].items()))+'\n\n[检查清单](checks.md) · [问题台账](issues.md) · [证据索引](evidence.md) · [任务状态](tasks.md) · [资料清单](sources.md)\n\n结构校验及复核记录见 validation.json、quality-review.json。程序不证明工程正确、范围完整或法定审查合格。',
      'checks': '# 检查清单\n\n'+summary+'\n\n'+table(['ID','范围','规则','目标/达到','结果','证据','说明'],[[c['check_id'],c['scope_id'],c['rule_id'],c['target_depth']+'/'+c['achieved_depth'],c['result'],', '.join(c['evidence_ids']),c.get('execution_note','')] for c in data['checks']]),
      'issues': '# 问题台账\n\n'+'\n\n'.join('## '+i['issue_id']+' '+i['title']+'\n\n'+f"{i['severity']} / {i['judgement']} / {i['status']}\n\n"+i['finding']+'\n\n影响：'+i['impact']+'\n\n设计答复事项：'+i['question_to_designer']+'\n\n证据：'+', '.join(i['evidence_ids']) for i in data['issues']),
      'evidence': '# 证据索引\n\n'+table(['ID','来源/图面','等级','定位','说明'],[[e['evidence_id'],e['source_id']+'/'+str(e['sheet_id']),e['grade'],e['locator'],e.get('note','')] for e in data['evidence']]),
      'sources': '# 资料清单\n\n'+table(['来源','读取状态','原件路径','限制'],[[s['source_id'],s['read_status'],s['path'],'；'.join(s['limitations'])] for s in data['sources']]),
      'tasks': '# 综合任务状态\n\n综合任务只汇总，不与检查记录重复计数。complete表示检查已完成，不表示问题关闭。\n\n'+(table(['任务','名称','状态','完成/适用'],[[tid,t['label'],t['status'],str(t['completed_checks'])+'/'+str(t['applicable_checks'])] for tid,t in m['task_summaries'].items()]) if m['task_summaries'] else '历史台账未声明结构化任务层级；未自动推断父子关系。')}
    for name,content in pages.items():
        write(folder/(name+'.md'),content)
        # Dependency-free readable HTML; every byte is escaped before display.
        nav=' · '.join('<a href="'+n+'.html">'+html.escape(n)+'</a>' for n in pages)
        write(folder/(name+'.html'),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(name)+'</title><style>body{max-width:1200px;margin:32px auto;padding:24px;font:16px/1.7 sans-serif}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}</style><nav>'+nav+'</nav><pre>'+html.escape(content)+'</pre></html>')


def publish(project, review_path, qa_path, expected_revision):
    with lock(project) as project:
        state=load_state(project);expect(state,expected_revision)
        p,data,v=checked(review_path); sha=v['review_sha256']; manifest=artifacts(data,p.parent);q=read(qa_path)
        verify_qa(q,data,sha,manifest)
        reserved={'review.json','quality-review.json','validation.json','manifest.json'}|{n+ext for n in ['report','checks','issues','evidence','sources','tasks'] for ext in ['.md','.html']}
        if any(name in reserved for name in manifest): raise ValueError('证据文件名与发布输出冲突；请改用 evidence 等子目录')
        folder=project/'releases'/uuid.uuid4().hex;folder.mkdir(parents=True)
        # Snapshot is never the source round, and is not visible through the pointer yet.
        shutil.copyfile(p,folder/'review.json')
        for rel,h in manifest.items():
            dst=folder/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p.parent/rel,dst)
            if digest(dst)!=h:raise ValueError('复制期间证据变化')
        if digest(folder/'review.json')!=sha:raise ValueError('复制期间台账变化')
        write(folder/'quality-review.json',q);write(folder/'validation.json',v);reports(folder,data,v)
        # Re-read source fingerprints immediately before committing publication.
        if digest(p)!=sha or artifacts(data,p.parent)!=manifest:raise ValueError('发布期间输入发生变化')
        final=validate(read(folder/'review.json'),True,folder)
        if not final['valid']:raise ValueError('快照校验失败')
        write(folder/'validation.json',final)
        files={f.relative_to(folder).as_posix():digest(f) for f in folder.rglob('*') if f.is_file()}
        write(folder/'manifest.json',dict(created_at=now(),files=files,review_sha256=sha))
        latest=dict(kind='release',review=str(folder/'review.json'),report=str(folder/'report.html'),manifest=str(folder/'manifest.json'),manifest_sha256=digest(folder/'manifest.json'),review_sha256=sha,metrics=final['metrics'])
        state=dict(state or {'schema_version':'1.0'},latest_validated=latest)
        return commit(project,state)


def register_baseline(project, review_path, qa_path, expected_revision):
    """Register a legacy validated round; never upgrade work-in-progress into a release."""
    with lock(project) as project:
        state=load_state(project);expect(state,expected_revision)
        if state and state.get('latest_validated'):raise ValueError('已有正式入口，禁止用历史登记替换')
        p,data,v=checked(review_path);q=read(qa_path)
        if q.get('valid') is not True or not (p.parent/'report.html').is_file():raise ValueError('缺少已有图面复核记录或历史报告')
        state=dict(state or {'schema_version':'1.0'},latest_validated=dict(kind='legacy_baseline',review=str(p),report=str(p.parent/'report.html'),review_sha256=v['review_sha256'],legacy_report_sha256=digest(p.parent/'report.html'),legacy_qa=str(Path(qa_path).resolve()),legacy_qa_sha256=digest(qa_path),metrics=v['metrics'],limitation='复用历史图面复核；本次仅重验结构、附件和源哈希，未重新开展工程审核。'))
        return commit(project,state)


def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('status');p.add_argument('project')
    p=sub.add_parser('checkpoint');p.add_argument('project');p.add_argument('--patch',required=True);p.add_argument('--expected-revision',default=None)
    p=sub.add_parser('prepare-qa');p.add_argument('review');p.add_argument('--output',required=True)
    for cmd in ['publish','register-baseline']:
        p=sub.add_parser(cmd);p.add_argument('project');p.add_argument('--review',required=True);p.add_argument('--qa',required=True);p.add_argument('--expected-revision',default=None)
    args=ap.parse_args()
    try:
        if args.command=='status':result=status(args.project)
        elif args.command=='checkpoint':result=checkpoint(args.project,read(args.patch),args.expected_revision)
        elif args.command=='prepare-qa':result=prepare_qa(args.review,args.output)
        elif args.command=='publish':result=publish(args.project,args.review,args.qa,args.expected_revision)
        else:result=register_baseline(args.project,args.review,args.qa,args.expected_revision)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps(dict(error=str(exc)),ensure_ascii=False));return 1

if __name__=='__main__':raise SystemExit(main())
