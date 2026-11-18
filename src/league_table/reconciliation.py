"""Observation reconciliation: global feasible assignments and Hall certificates.

The candidate graph is bipartite: left vertices are observations (sorted by
observation id), right vertices are fixtures (sorted by fixture id).  A legal
complete assignment saturates every observation and uses every fixture at most
once.  Assignments are never enumerated.

* A maximum matching (Hopcroft-Karp) decides whether any complete assignment
  exists.
* An edge may occur in some maximum matching iff it is matched, its endpoints
  lie in one strongly connected component of the matching digraph, or its
  fixture endpoint can reach an unmatched fixture in the matching digraph.
  This standard characterisation gives, for each observation, exactly the set
  of fixtures it can take over all legal complete assignments.
* When no complete assignment exists, the Dulmage-Mendelsohn set S of
  observations reachable from unmatched observations by alternating paths
  satisfies |N(S)| < |S| by Koenig's theorem, and is reported as the
  certificate.
"""
from collections import deque

from .common import ident, number, require, rows

def _maximum_matching(adjacency):
    """Hopcroft-Karp on sorted adjacency lists; returns pairs, size, width."""
    n=len(adjacency);m=max((v for a in adjacency for v in a),default=-1)+1
    pair_left=[-1]*n;pair_right=[-1]*m;distance=[0]*n;infinity=n+1
    def layer():
        queue=deque();reachable=False
        for left in range(n):
            if pair_left[left]<0:distance[left]=0;queue.append(left)
            else:distance[left]=infinity
        while queue:
            left=queue.popleft()
            for right in adjacency[left]:
                other=pair_right[right]
                if other<0:reachable=True
                elif distance[other]==infinity:
                    distance[other]=distance[left]+1;queue.append(other)
        return reachable
    def augment(root):
        stack=[root];positions=[0];chosen=[]
        while stack:
            left=stack[-1];index=positions[-1]
            if index<len(adjacency[left]):
                right=adjacency[left][index];positions[-1]=index+1;other=pair_right[right]
                if other<0:
                    pair_left[left]=right;pair_right[right]=left
                    for depth in range(len(stack)-2,-1,-1):
                        owner=stack[depth];reserved=chosen[depth]
                        pair_left[owner]=reserved;pair_right[reserved]=owner
                    return True
                if distance[other]==distance[left]+1:
                    chosen.append(right);stack.append(other);positions.append(0)
            else:
                distance[left]=infinity;stack.pop();positions.pop()
                if chosen:chosen.pop()
        return False
    size=0
    while layer():
        for left in range(n):
            if pair_left[left]<0 and augment(left):size+=1
    return pair_left,pair_right,size,m

def _strongly_connected_components(component,graph,reverse,total):
    seen=[False]*total;order=[]
    for start in range(total):
        if seen[start]:continue
        seen[start]=True;stack=[(start,0)]
        while stack:
            node,index=stack[-1]
            if index<len(graph[node]):
                nxt=graph[node][index];stack[-1]=(node,index+1)
                if not seen[nxt]:seen[nxt]=True;stack.append((nxt,0))
            else:order.append(node);stack.pop()
    count=0
    for start in reversed(order):
        if component[start]>=0:continue
        component[start]=count;stack=[start]
        while stack:
            node=stack.pop()
            for nxt in reverse[node]:
                if component[nxt]<0:component[nxt]=count;stack.append(nxt)
        count+=1
    return count

def _allowed_edges(adjacency,pair_left,pair_right,n,m):
    total=n+m;graph=[[] for _ in range(total)];reverse=[[] for _ in range(total)]
    def link(a,b):graph[a].append(b);reverse[b].append(a)
    for left in range(n):
        for right in adjacency[left]:link(left,n+right)
        if pair_left[left]>=0:link(n+pair_left[left],left)
    component=[-1]*total;_strongly_connected_components(component,graph,reverse,total)
    reachable=[False]*total;stack=[]
    for right in range(m):
        if pair_right[right]<0:reachable[n+right]=True;stack.append(n+right)
    while stack:
        node=stack.pop()
        for nxt in reverse[node]:
            if not reachable[nxt]:reachable[nxt]=True;stack.append(nxt)
    allowed=[]
    for left in range(n):
        options=set()
        for right in adjacency[left]:
            if (pair_left[left]==right or component[left]==component[n+right]
                    or reachable[n+right]):options.add(right)
        allowed.append(options)
    return allowed

def _hall_witness(adjacency,pair_left,pair_right,n,m):
    reachable=[False]*n;seen_right=[False]*m;queue=deque()
    for left in range(n):
        if pair_left[left]<0:reachable[left]=True;queue.append(left)
    while queue:
        left=queue.popleft()
        for right in adjacency[left]:
            if pair_left[left]==right or seen_right[right]:continue
            seen_right[right]=True;other=pair_right[right]
            if other>=0 and not reachable[other]:reachable[other]=True;queue.append(other)
    observations=[left for left in range(n) if reachable[left]]
    fixtures=set()
    for left in observations:fixtures.update(adjacency[left])
    return observations,sorted(fixtures)

def validate(teams,fixtures,results,data):
    """Validate the observations envelope against the trusted snapshot."""
    require(data,'observations')
    known=set(teams);locked=set(results);records=[];seen=set();total=0
    for item in rows(data['observations'],2000):
        require(item,'id side_a side_b a_goals b_goals candidates')
        oid=ident(item['id'])
        if oid in seen:raise ValueError('duplicate observation id')
        seen.add(oid)
        side_a=ident(item['side_a']);side_b=ident(item['side_b'])
        if side_a not in known or side_b not in known:raise ValueError('unknown observation team')
        if side_a==side_b:raise ValueError('observation teams must differ')
        a_goals=number(item['a_goals'],0,1000);b_goals=number(item['b_goals'],0,1000)
        candidates=[];chosen=set()
        for raw in rows(item['candidates'],20000):
            fid=ident(raw)
            if fid in chosen:raise ValueError('duplicate observation candidate')
            chosen.add(fid);total+=1
            if total>20000:raise ValueError('too many observation candidates')
            if fid not in fixtures:raise ValueError('unknown observation candidate')
            if fid in locked:raise ValueError('candidate already has a formal result')
            home,away=fixtures[fid]
            if {home,away}!={side_a,side_b}:raise ValueError('candidate sides do not match observation teams')
            candidates.append(fid)
        records.append({'id':oid,'side_a':side_a,'side_b':side_b,'a_goals':a_goals,
                        'b_goals':b_goals,'candidates':candidates})
    records.sort(key=lambda record:record['id'])
    return records

def reconcile(fixtures,records):
    """Return (reconciliation, imported_results)."""
    records=sorted(records,key=lambda record:record['id'])
    if not records:return {'status':'feasible','observations':[],'hall_witness':None},{}
    fixture_ids=sorted({fid for record in records for fid in record['candidates']})
    index={fid:position for position,fid in enumerate(fixture_ids)}
    adjacency=[sorted({index[fid] for fid in record['candidates']}) for record in records]
    n=len(records)
    pair_left,pair_right,size,m=_maximum_matching(adjacency)
    if size<n:
        observations,fixtures_used=_hall_witness(adjacency,pair_left,pair_right,n,m)
        return ({'status':'infeasible','observations':[],
                 'hall_witness':{'observation_ids':[records[i]['id'] for i in observations],
                                 'fixture_ids':[fixture_ids[j] for j in fixtures_used]}},{})
    allowed=_allowed_edges(adjacency,pair_left,pair_right,n,m)
    entries=[];imported={}
    for position,record in enumerate(records):
        possible=[fixture_ids[j] for j in sorted(allowed[position])]
        resolved=possible[0] if len(possible)==1 else None
        entries.append({'id':record['id'],'possible_fixtures':possible,'resolved_fixture':resolved})
        if resolved is not None:
            home,away=fixtures[resolved]
            a_goals=record['a_goals'];b_goals=record['b_goals']
            imported[resolved]=(a_goals,b_goals) if home==record['side_a'] else (b_goals,a_goals)
    return {'status':'feasible','observations':entries,'hall_witness':None},imported
