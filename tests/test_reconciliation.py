import json,random,tempfile,unittest
from pathlib import Path
from league_table.cli import run
from league_table.model import analyze,parse
from league_table.reconciliation import _allowed_edges,_hall_witness,_maximum_matching,reconcile,validate
from league_table.standings import calculate
ROOT=Path(__file__).resolve().parents[1]
def fixture():return json.loads((ROOT/'fixtures/input.json').read_text())
def snapshot(teams,fixtures,results,deductions=()):
 return {'teams':teams,'fixtures':fixtures,'results':results,'deductions':list(deductions)}
def two_sided():
 return snapshot(['A','B','C'],
  [{'id':'m1','home':'A','away':'B'},{'id':'m2','home':'B','away':'A'},
   {'id':'m3','home':'B','away':'C'},{'id':'x','home':'A','away':'C'}],
  [{'fixture':'x','home_goals':3,'away_goals':0}])
def observation(oid,candidates,side_a='A',side_b='B',a_goals=2,b_goals=1):
 return {'id':oid,'side_a':side_a,'side_b':side_b,'a_goals':a_goals,'b_goals':b_goals,
         'candidates':list(candidates)}
def check(data,observations):
 teams,fixtures,results,deductions=parse(data)
 records=validate(teams,fixtures,results,{'observations':observations})
 reconciliation,imported=reconcile(fixtures,records)
 merged=dict(results);merged.update(imported)
 return reconciliation,imported,calculate(teams,fixtures,merged,deductions)
class Reconciliation(unittest.TestCase):
 def test_schema_v2_envelope(self):
  data=fixture();observations=[observation('o1',['m4'],side_a='D',side_b='A')]
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'obs.json';path.write_text(json.dumps({'observations':observations}))
   report=run(ROOT/'fixtures/input.json',path)
  self.assertEqual(report['schema_version'],2)
  self.assertEqual(set(report),{'schema_version','source_sha256','observations_sha256','result','reconciliation'})
  self.assertEqual(set(report['reconciliation']),{'status','observations','hall_witness'})
 def test_legacy_envelope_unchanged(self):
  report=run(ROOT/'fixtures/input.json')
  self.assertEqual(report['schema_version'],1);self.assertEqual(set(report),{'schema_version','source_sha256','result'})
 def test_legacy_result_matches_analyze(self):
  self.assertEqual(run(ROOT/'fixtures/input.json')['result'],analyze(fixture()))
 def test_both_unique(self):
  reconciliation,imported,result=check(two_sided(),
   [observation('o1',['m1','m2']),observation('o2',['m2'])])
  self.assertEqual(reconciliation['status'],'feasible');self.assertIsNone(reconciliation['hall_witness'])
  self.assertEqual([(o['id'],o['possible_fixtures'],o['resolved_fixture']) for o in reconciliation['observations']],
                   [('o1',['m1'],'m1'),('o2',['m2'],'m2')])
  self.assertEqual(imported,{'m1':(2,1),'m2':(1,2)})
  self.assertEqual(result['completed'],3)
 def test_equal_scores_remain_ambiguous(self):
  reconciliation,imported,_=check(two_sided(),
   [observation('o1',['m1','m2']),observation('o2',['m1','m2'])])
  self.assertEqual(reconciliation['status'],'feasible');self.assertEqual(imported,{})
  self.assertEqual([o['possible_fixtures'] for o in reconciliation['observations']],[['m1','m2'],['m1','m2']])
  self.assertEqual([o['resolved_fixture'] for o in reconciliation['observations']],[None,None])
 def test_identical_observations_not_merged(self):
  reconciliation,imported,_=check(two_sided(),
   [observation('o1',['m1']),observation('o2',['m2'],a_goals=2,b_goals=1)])
  self.assertEqual(reconciliation['status'],'feasible')
  self.assertEqual([o['id'] for o in reconciliation['observations']],['o1','o2'])
  self.assertEqual(imported,{'m1':(2,1),'m2':(1,2)})
 def test_single_observation_lists_all_fixtures(self):
  reconciliation,imported,_=check(two_sided(),[observation('o1',['m1','m2'])])
  self.assertEqual(reconciliation['observations'],[{'id':'o1','possible_fixtures':['m1','m2'],'resolved_fixture':None}])
  self.assertEqual(imported,{})
 def test_reversed_home_away_import(self):
  reconciliation,imported,_=check(two_sided(),
   [observation('o1',['m1'],side_a='B',side_b='A',a_goals=0,b_goals=2)])
  self.assertEqual(reconciliation['observations'][0]['resolved_fixture'],'m1')
  self.assertEqual(imported,{'m1':(2,0)})
 def test_infeasible_certificate(self):
  reconciliation,imported,result=check(two_sided(),
   [observation('o1',['m1']),observation('o2',['m1'])])
  self.assertEqual(reconciliation['status'],'infeasible');self.assertEqual(reconciliation['observations'],[])
  self.assertEqual(reconciliation['hall_witness'],{'observation_ids':['o1','o2'],'fixture_ids':['m1']})
  self.assertEqual(imported,{});self.assertEqual(result,analyze(two_sided()))
 def test_certificate_has_full_neighbourhood(self):
  data=snapshot(['A','B'],[{'id':f,'home':'A','away':'B'} for f in ('m1','m2','m3')],[])
  observations=[observation('o1',['m1','m2']),observation('o2',['m1']),observation('o3',['m2'])]
  reconciliation,imported,result=check(data,observations)
  self.assertEqual(reconciliation['status'],'infeasible')
  witness=reconciliation['hall_witness']
  self.assertEqual(witness,{'observation_ids':['o1','o2','o3'],'fixture_ids':['m1','m2']})
  neighbourhood=set()
  for item in observations:
   if item['id'] in witness['observation_ids']:neighbourhood.update(item['candidates'])
  self.assertEqual(set(witness['fixture_ids']),neighbourhood)
  self.assertLess(len(witness['fixture_ids']),len(witness['observation_ids']))
  self.assertEqual(imported,{});self.assertEqual(result,analyze(data))
 def test_empty_observations(self):
  reconciliation,imported,result=check(fixture(),[])
  self.assertEqual(reconciliation,{'status':'feasible','observations':[],'hall_witness':None})
  self.assertEqual(imported,{});self.assertEqual(result,analyze(fixture()))
 def test_results_are_locked(self):
  data=two_sided()
  with self.assertRaises(ValueError):check(data,[observation('o1',['x'])])
 def test_unknown_team_rejected(self):
  for field,value in (('side_a','Z'),('side_b','Z')):
   values=observation('o1',['m1']);values[field]=value
   with self.assertRaises(ValueError):check(two_sided(),[values])
 def test_same_team_rejected(self):
  with self.assertRaises(ValueError):check(two_sided(),[observation('o1',['m1'],side_a='A',side_b='A')])
 def test_candidate_side_mismatch(self):
  with self.assertRaises(ValueError):check(two_sided(),[observation('o1',['m3'])])
 def test_duplicate_candidates_rejected(self):
  with self.assertRaises(ValueError):check(two_sided(),[observation('o1',['m1','m1'])])
 def test_duplicate_observation_ids_rejected(self):
  with self.assertRaises(ValueError):check(two_sided(),[observation('o1',['m1']),observation('o1',['m2'])])
 def test_unknown_candidate_rejected(self):
  with self.assertRaises(ValueError):check(two_sided(),[observation('o1',['nope'])])
 def test_goals_bounds_and_bool(self):
  for field,value in (('a_goals',-1),('b_goals',1001),('a_goals',True),('b_goals',False),('a_goals',1.0)):
   values=observation('o1',['m1']);values[field]=value
   with self.assertRaises(ValueError):check(two_sided(),[values])
 def test_observation_unknown_field_rejected(self):
  values=observation('o1',['m1']);values['extra']=1
  with self.assertRaises(ValueError):check(two_sided(),[values])
 def test_team_limit_unchanged(self):
  data=two_sided();data['teams']=[f't{i}' for i in range(201)]
  with self.assertRaises(ValueError):analyze(data)
 def test_observation_limit(self):
  data=snapshot(['A','B'],[{'id':'m','home':'A','away':'B'}],[])
  many=[observation(f'o{i:04d}',['m']) for i in range(2001)]
  with self.assertRaises(ValueError):check(data,many)
 def test_candidate_total_limit(self):
  fixtures=[{'id':f'f{i:04d}','home':'A','away':'B'} for i in range(2000)]
  data=snapshot(['A','B'],fixtures,[])
  entries=[f'f{i:04d}' for i in range(2000)]
  many=[observation(f'o{i:04d}',[entries[(i+k)%2000] for k in range(10)]) for i in range(2000)]
  reconciliation,_,_=check(data,many)  # exactly 2000 observations and 20000 edges
  self.assertEqual(reconciliation['status'],'feasible')
  self.assertEqual(len(reconciliation['observations']),2000)
  self.assertEqual(reconciliation['observations'][0]['possible_fixtures'],
                   sorted([entries[k] for k in range(10)]))
  overflow=many+[observation('o9999',['f000'])]
  with self.assertRaises(ValueError):check(data,overflow)
 def test_candidate_bounds_match_fixtures(self):
  fixtures=[{'id':f'f{i:05d}','home':'A','away':'B'} for i in range(20000)]
  data=snapshot(['A','B'],fixtures,[])
  reconciliation,_,_=check(data,[observation('o1',[f'f{i:05d}' for i in range(20000)])])
  self.assertEqual(reconciliation['status'],'feasible')
  self.assertIsNone(reconciliation['observations'][0]['resolved_fixture'])
 def test_permutation_stability(self):
  observations=[observation('o3',['m1','m2']),observation('o1',['m1']),observation('o2',['m2'])]
  forward,_,_=check(two_sided(),observations)
  backward,_,_=check(two_sided(),list(reversed(observations)))
  self.assertEqual(forward,backward)
 def test_certificate_permutation_stability(self):
  data=snapshot(['A','B'],[{'id':f,'home':'A','away':'B'} for f in ('m1','m2','m3')],[])
  observations=[observation('o2',['m1']),observation('o1',['m1','m2']),observation('o3',['m2'])]
  forward,_,_=check(data,observations)
  backward,_,_=check(data,list(reversed(observations)))
  self.assertEqual(forward,backward)
  self.assertEqual(forward['hall_witness'],{'observation_ids':['o1','o2','o3'],'fixture_ids':['m1','m2']})
 def test_legacy_results_untouched_when_ambiguous(self):
  data=two_sided();_,imported,result=check(data,[observation('o1',['m1','m2'])])
  self.assertEqual(imported,{});self.assertEqual(result,analyze(data))
 def test_import_merges_only_resolved(self):
  data=fixture()
  _,imported,result=check(data,[observation('o1',['m4'],side_a='A',side_b='D',a_goals=1,b_goals=1)])
  self.assertEqual(imported,{'m4':(1,1)});self.assertEqual(result['completed'],4);self.assertNotIn('m4',result['pending_fixtures'])
 def test_allowed_edges_matches_enumeration(self):
  random.seed(7)
  for _ in range(400):
   n=random.randint(0,5);m=random.randint(0,6)
   adjacency=[sorted(v for v in range(m) if random.random()<0.5) for _ in range(n)]
   pair_left,pair_right,size,width=_maximum_matching(adjacency)
   if size<n:continue
   allowed=_allowed_edges(adjacency,pair_left,pair_right,n,width)
   expected=AND_enumeration(adjacency,n,m)
   self.assertEqual(allowed,expected)
 def test_hall_witness_is_verifiable(self):
  random.seed(13)
  for _ in range(400):
   n=random.randint(1,6);m=random.randint(0,6)
   adjacency=[sorted(v for v in range(m) if random.random()<0.45) for _ in range(n)]
   pair_left,pair_right,size,width=_maximum_matching(adjacency)
   if size==n:continue
   observations,fixtures_used=_hall_witness(adjacency,pair_left,pair_right,n,width)
   self.assertTrue(observations)
   self.assertEqual(fixtures_used,sorted({v for left in observations for v in adjacency[left]}))
   self.assertLess(len(fixtures_used),len(observations))
 def test_random_matching_size_matches_bruteforce(self):
  random.seed(99)
  for _ in range(200):
   n=random.randint(0,6);m=random.randint(0,6)
   adjacency=[sorted(v for v in range(m) if random.random()<0.5) for _ in range(n)]
   _,_,size,_=_maximum_matching(adjacency)
   self.assertEqual(size,maximum_size(adjacency,n))
 def test_random_input_permutation_stability(self):
  random.seed(21)
  data=snapshot(['A','B','C'],[{'id':f'm{i:02d}','home':'A','away':'B'} for i in range(8)]+
                        [{'id':f'n{i:02d}','home':'B','away':'C'} for i in range(4)],[])
  observations=[]
  for index in range(10):
   pool=[f'm{i:02d}' for i in range(8)] if index%3 else [f'n{i:02d}' for i in range(4)]
   picks=sorted({random.choice(pool) for _ in range(random.randint(1,4))})
   observations.append(observation(f'o{index:02d}',picks,
                                  side_a='A' if index%3 else 'B',
                                  side_b='B' if index%3 else 'C'))
  forward,_,forward_result=check(data,observations)
  shuffled=list(observations);random.shuffle(shuffled)
  for _ in range(5):
   again,_,again_result=check(data,shuffled);random.shuffle(shuffled)
   self.assertEqual(forward,again);self.assertEqual(forward_result,again_result)
def maximum_size(adjacency,n):
 best=0
 def rec(index,used,count):
  nonlocal best
  if index==n:
   best=max(best,count);return
  rec(index+1,used,count)
  for right in adjacency[index]:
   if right not in used:
    used.add(right);rec(index+1,used,count+1);used.remove(right)
 rec(0,set(),0)
 return best
def AND_enumeration(adjacency,n,m):
 poss=[set() for _ in range(n)]
 def rec(index,used,picks):
  if index==n:
   for left,right in picks:poss[left].add(right)
   return
  for right in adjacency[index]:
   if right not in used:
    used.add(right);picks.append((index,right));rec(index+1,used,picks);picks.pop();used.remove(right)
 rec(0,set(),[])
 return poss

if __name__=='__main__':unittest.main()
