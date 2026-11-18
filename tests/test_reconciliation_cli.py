import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from league_table.cli import run
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'fixtures/input.json'
def observation(oid,candidates,side_a='A',side_b='D',a_goals=1,b_goals=1):
 return {'id':oid,'side_a':side_a,'side_b':side_b,'a_goals':a_goals,'b_goals':b_goals,
         'candidates':list(candidates)}
def invoke(p,out,observations=None):
 arguments=[sys.executable,'-m','league_table.cli','--source',str(p),'--output',str(out)]
 if observations is not None:arguments.extend(['--observations',str(observations)])
 return subprocess.run(arguments,capture_output=True)
class ReconciliationCLI(unittest.TestCase):
 def write(self,path,payload):path.write_text(json.dumps(payload));return path
 def test_legacy_invocation_schema_v1(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'report.json'
   before=out.read_text() if out.exists() else None
   c=invoke(SOURCE,out)
   self.assertEqual(c.returncode,0)
   report=json.loads(out.read_text())
   self.assertEqual(report['schema_version'],1);self.assertNotIn('observations_sha256',report)
   self.assertNotIn('reconciliation',report);self.assertIsNone(before)
 def test_observation_invocation_schema_v2(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[observation('o1',['m4'])]});out=p/'report.json'
   c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,0,c.stderr)
   report=json.loads(out.read_text())
   self.assertEqual(report['schema_version'],2)
   self.assertEqual(set(report),{'schema_version','source_sha256','observations_sha256','result','reconciliation'})
   self.assertEqual(report['result']['completed'],4)
   self.assertEqual(report['reconciliation']['observations'],
                    [{'id':'o1','possible_fixtures':['m4'],'resolved_fixture':'m4'}])
 def test_digests_are_byte_exact(self):
  import hashlib
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[observation('o1',['m4'])]});out=p/'report.json'
   invoke(SOURCE,out,obs);report=json.loads(out.read_text())
   self.assertEqual(report['source_sha256'],hashlib.sha256(SOURCE.read_bytes()).hexdigest())
   self.assertEqual(report['observations_sha256'],hashlib.sha256(obs.read_bytes()).hexdigest())
 def test_infeasible_exits_zero_and_keeps_baseline(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)
   obs=self.write(p/'obs.json',{'observations':[observation('o1',['m4']),observation('o2',['m4'])]})
   out=p/'report.json';c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,0,c.stderr)
   report=json.loads(out.read_text())
   self.assertEqual(report['reconciliation']['status'],'infeasible')
   self.assertEqual(report['reconciliation']['observations'],[])
   self.assertEqual(report['reconciliation']['hall_witness'],
                    {'observation_ids':['o1','o2'],'fixture_ids':['m4']})
   self.assertEqual(report['result'],run(SOURCE)['result'])
 def test_empty_observations(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[]});out=p/'report.json'
   c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,0,c.stderr)
   report=json.loads(out.read_text())
   self.assertEqual(report['reconciliation'],{'status':'feasible','observations':[],'hall_witness':None})
   self.assertEqual(report['result'],run(SOURCE)['result'])
 def test_output_cannot_equal_observations(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[]});old=obs.read_bytes()
   c=invoke(SOURCE,obs,obs);self.assertEqual(c.returncode,2);self.assertEqual(obs.read_bytes(),old)
 def test_failure_preserves_old_report(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[observation('o1',['missing'])]})
   out=p/'report.json';out.write_text('old')
   c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,2)
   self.assertEqual(out.read_text(),'old')
 def test_unknown_observation_field_fails(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);payload={'observations':[observation('o1',['m4'])]};payload['observations'][0]['extra']=1
   obs=self.write(p/'obs.json',payload);out=p/'report.json';out.write_text('old')
   c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,2);self.assertEqual(out.read_text(),'old')
 def test_duplicate_json_key_fails(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=p/'obs.json';obs.write_text('{"observations":[],"observations":[]}')
   out=p/'report.json';out.write_text('old')
   c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,2);self.assertEqual(out.read_text(),'old')
 def test_locked_result_candidate_fails(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[observation('o1',['m1'])]})
   out=p/'report.json';out.write_text('old')
   c=invoke(SOURCE,out,obs);self.assertEqual(c.returncode,2);self.assertEqual(out.read_text(),'old')
 def test_input_permutation_stability(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)
   observations=[observation('o3',['m4']),observation('o1',['m4']),observation('o2',['m4'])]
   first=self.write(p/'a.json',{'observations':observations})
   second=self.write(p/'b.json',{'observations':list(reversed(observations))})
   reports=[]
   for index,obs in enumerate((first,second)):
    out=p/f'r{index}.json';invoke(SOURCE,out,obs);reports.append(json.loads(out.read_text()))
   self.assertEqual(reports[0]['reconciliation'],reports[1]['reconciliation'])
   self.assertEqual(reports[0]['result'],reports[1]['result'])
 def test_output_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);obs=self.write(p/'obs.json',{'observations':[]});link=p/'link';link.symlink_to(SOURCE)
   c=invoke(SOURCE,link,obs);self.assertEqual(c.returncode,2)
 def test_shipped_examples(self):
  source=ROOT/'fixtures/input-reconciliation.json'
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)
   cases={'observations.json':('feasible',[('o1','m1'),('o2','m2')],None),
          'observations-ambiguous.json':('feasible',[('o1',None)],None),
          'observations-infeasible.json':('infeasible',[],
                                          {'observation_ids':['o1','o2'],'fixture_ids':['m1']})}
   for name,(status,resolved,witness) in cases.items():
    out=p/(name+'.report');c=invoke(source,out,ROOT/'fixtures'/name);self.assertEqual(c.returncode,0,c.stderr)
    report=json.loads(out.read_text())['reconciliation']
    self.assertEqual(report['status'],status);self.assertEqual(report['hall_witness'],witness)
    if status=='feasible':
     self.assertEqual([(o['id'],o['resolved_fixture']) for o in report['observations']],resolved)
    self.assertEqual(json.loads(out.read_text())['result']['completed'],
                     3 if status=='feasible' and name=='observations.json' else 1)
 def test_missing_observations_preserves_output(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);out=p/'report.json';out.write_text('old')
   c=invoke(SOURCE,out,p/'absent.json');self.assertEqual(c.returncode,2);self.assertEqual(out.read_text(),'old')
 def test_candidate_order_does_not_matter(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);source=ROOT/'fixtures/input-reconciliation.json'
   first=self.write(p/'a.json',{'observations':[observation('o1',['m2','m1'],side_a='A',side_b='B')]})
   second=self.write(p/'b.json',{'observations':[observation('o1',['m1','m2'],side_a='A',side_b='B')]})
   reports=[]
   for index,obs in enumerate((first,second)):
    out=p/f'r{index}.json';invoke(source,out,obs);reports.append(json.loads(out.read_text()))
   self.assertEqual(reports[0]['reconciliation'],reports[1]['reconciliation'])
   self.assertEqual(reports[0]['reconciliation']['observations'][0]['possible_fixtures'],['m1','m2'])
   self.assertNotEqual(reports[0]['observations_sha256'],reports[1]['observations_sha256'])
if __name__=='__main__':unittest.main()
