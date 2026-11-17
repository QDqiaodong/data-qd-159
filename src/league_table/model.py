from .common import require,ident,rows,number
from .standings import calculate

def analyze(data):
    require(data,'teams fixtures results deductions');teams=[ident(t) for t in rows(data['teams'],200)]
    if not teams or len(set(teams))!=len(teams):raise ValueError('nonempty unique teams required')
    fixtures={};results={};deductions={}
    for f in rows(data['fixtures'],20000):
        require(f,'id home away');fid=ident(f['id']);home=ident(f['home']);away=ident(f['away'])
        if fid in fixtures or home not in teams or away not in teams or home==away:raise ValueError('invalid fixture identity/sides')
        fixtures[fid]=(home,away)
    for r in rows(data['results'],20000):
        require(r,'fixture home_goals away_goals');fid=ident(r['fixture'])
        if fid in results or fid not in fixtures:raise ValueError('duplicate or unknown result')
        results[fid]=(number(r['home_goals'],0,1000),number(r['away_goals'],0,1000))
    for d in rows(data['deductions'],200):
        require(d,'team points reason');team=ident(d['team']);ident(d['reason'])
        if team not in teams or team in deductions:raise ValueError('duplicate or unknown deduction')
        deductions[team]=number(d['points'],0,1000)
    return calculate(teams,fixtures,results,deductions)
