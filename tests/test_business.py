import unittest,json
from pathlib import Path
from league_table.model import analyze
ROOT=Path(__file__).resolve().parents[1]
def fixture():return json.loads((ROOT/'fixtures/input.json').read_text())
class League(unittest.TestCase):
 def test_standings_and_pending(self):
  r=analyze(fixture());self.assertEqual([(x['team'],x['points']) for x in r['standings']],[('C',4),('A',2),('B',1),('D',0)]);self.assertEqual(r['pending_fixtures'],['m4'])
 def test_global_conservation(self):
  r=analyze(fixture());rows=r['standings'];self.assertEqual(sum(x['goals_for'] for x in rows),sum(x['goals_against'] for x in rows));self.assertEqual(sum(x['played'] for x in rows),2*r['completed']);self.assertEqual(sum(x['won'] for x in rows),sum(x['lost'] for x in rows))
 def test_tied_rank(self):
  d=fixture();d['results']=[];d['deductions']=[];self.assertEqual([x['rank'] for x in analyze(d)['standings']],[1,1,1,1])
 def test_competition_rank_gap(self):
  d={'teams':['A','B','C'],'fixtures':[{'id':'m','home':'A','away':'B'}],'results':[{'fixture':'m','home_goals':0,'away_goals':0}],'deductions':[]};self.assertEqual([x['rank'] for x in analyze(d)['standings']],[1,1,3])
 def test_duplicate_result(self):
  d=fixture();d['results'].append(d['results'][0])
  with self.assertRaises(ValueError):analyze(d)
 def test_unknown_fixture(self):
  d=fixture();d['results'][0]['fixture']='missing'
  with self.assertRaises(ValueError):analyze(d)
 def test_invalid_goals(self):
  for value in [-1,True,1.5]:
   d=fixture();d['results'][0]['home_goals']=value
   with self.assertRaises(ValueError):analyze(d)
 def test_self_fixture(self):
  d=fixture();d['fixtures'][0]['away']='A'
  with self.assertRaises(ValueError):analyze(d)
 def test_negative_points(self):
  d=fixture();d['deductions'][0]['points']=10;self.assertEqual(next(x for x in analyze(d)['standings'] if x['team']=='A')['points'],-7)
 def test_permutation(self):
  d=fixture();expected=analyze(d)
  for values in d.values():values.reverse()
  self.assertEqual(expected,analyze(d))
