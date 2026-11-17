def calculate(teams,fixtures,results,deductions):
    table={t:{'team':t,'played':0,'won':0,'drawn':0,'lost':0,'goals_for':0,'goals_against':0,'deduction':deductions.get(t,0)} for t in teams}
    pending=[]
    for fid,(home,away) in sorted(fixtures.items()):
        if fid not in results:pending.append(fid);continue
        h,a=results[fid]
        for team,opponent,gf,ga in ((home,away,h,a),(away,home,a,h)):
            row=table[team];row['played']+=1;row['goals_for']+=gf;row['goals_against']+=ga
            row['won']+=gf>ga;row['drawn']+=gf==ga;row['lost']+=gf<ga
    for row in table.values():
        row['goal_difference']=row['goals_for']-row['goals_against'];row['points']=row['won']*3+row['drawn']-row['deduction']
    out=sorted(table.values(),key=lambda r:(-r['points'],-r['goal_difference'],-r['goals_for'],r['team']))
    last=None;rank=0
    for index,row in enumerate(out,1):
        key=(row['points'],row['goal_difference'],row['goals_for'])
        if key!=last:rank=index;last=key
        row['rank']=rank
    return {'standings':out,'pending_fixtures':pending,'completed':len(results)}
