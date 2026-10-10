"""Reject unsupported assertions grounded only in names or sample cells.

This guard never supplies a description, never changes a native verdict, and
never infers absence of all useful description content. Definition/method text
continues through independent native semantic review.
"""
import csv,re
PROCESS=[('measurement',re.compile(r'\b(?:measure\w*|sensor\w*|sensed|sensing|gaug\w*)\b',re.I)),
 ('estimation',re.compile(r'\bestimat\w*\b',re.I)),
 ('simulation',re.compile(r'\bsimulat\w*\b',re.I)),
 ('model production',re.compile(r'\b(?:modeled|modelled|modeling|modelling)\b',re.I)),
 ('calculation',re.compile(r'\bcalculat\w*\b',re.I)),
 ('prediction',re.compile(r'\bpredict\w*\b',re.I))]
PROVENANCE=re.compile(r'\b(?:derived|obtained|sourced|taken|collected|retrieved|downloaded|originat(?:es|ed|ing))\s+(?:from|via)\b|\bfrom\s+(?:the\s+)?(?:[A-Za-z0-9_.-]+\s+){0,5}(?:dataset|database|repository|archive|collection)\b',re.I)
AGGREGATION_PROCESS=re.compile(r'\baggregat(?:ed|ing)\b',re.I)
NAMING_DERIVATION=re.compile(r'^\s*(?:the\s+|a\s+)?(?:(?:field|column|variable|table)\s+name|header(?:\s+(?:row|names?))?|file\s*name)\b',re.I)

def source_process_phrases(value,name):
    """Check asserted production/origin; retain lexical statistic/name reading."""
    found=[]
    for label,pattern,own_pattern in [('data provenance',PROVENANCE,PROVENANCE),
                                      ('aggregation production',AGGREGATION_PROCESS,re.compile(r'\baggregat\w*\b',re.I))]:
        if own_pattern.search(name):continue
        words=[]
        for match in pattern.finditer(value):
            if label=='data provenance' and NAMING_DERIVATION.search(value[match.end():]):continue
            # A genuine explicit uncertainty or negation is not an asserted
            # creation method. Keep this local to the containing clause.
            before=re.split(r'[.;,\n]',value[:match.start()])[-1]
            after=re.split(r'[.;,\n]',value[match.end():])[0]
            if re.search(r'\b(?:not|never|no|whether)\b',before,re.I) or re.search(r'\b(?:unknown|unconfirmed|unspecified|not stated)\b',after,re.I):continue
            words.append(match.group())
        if words:found.append((label,words))
    return found
def nonsemantic(unit):
    return unit.get('kind') in ['header','row'] or (unit.get('kind')=='embedded_metadata' and unit.get('declared_attribute') in ['name','value.datatype','encoding.fileType'])

STATISTICS={'mean':'mean','average':'mean','min':'min','minimum':'min',
            'max':'max','maximum':'max','sum':'sum','median':'median',
            'std':'std','standard deviation':'std','count':'count',
            'variance':'variance'}
STATISTIC=re.compile(r'\b(?:standard deviation|'+ '|'.join(k for k in STATISTICS if k!='standard deviation')+r')\b',re.I)
UNIVERSAL=re.compile(r'\b(?:(?:each|every)\s+(?:variable|field|column)|all\s+(?:variables|fields|columns))\b',re.I)
QUALIFIED=re.compile(r'\b(?:or|either|some|depending|where applicable|where available|when available|as applicable|as available|if available|not all|not every|not each|whether|unknown)\b',re.I)

def quantified_statistics(value,units):
    """Conservative check of a conjunctive, unqualified universal header claim.

    Only complete cited CSV headers are inspected. This supplies a source
    counterexample, never a replacement value. Unrecognized/qualified prose,
    alternative statistics and method definitions remain for native review.
    """
    headers=[]
    for unit in units:
        if unit.get('kind')!='header':continue
        rows=list(csv.reader(unit['text'].splitlines()))
        if len(rows)==1 and rows[0]:headers.append(rows[0])
    if not headers:return []
    findings=[]
    for sentence in re.split(r'(?<=[.!?])\s+|\n+',value):
        if not UNIVERSAL.search(sentence) or QUALIFIED.search(sentence):continue
        matches=list(STATISTIC.finditer(sentence))
        wanted={STATISTICS[m.group().lower()] for m in matches}
        if len(wanted)<2 or not re.search(r'\band\b',sentence[matches[0].end():matches[-1].start()],re.I):continue
        # Do not apply whole-header quantification to an explicitly limited
        # subpopulation (e.g. each variable with a mean suffix).
        if re.search(UNIVERSAL.pattern+r'\s+(?:that|which|with|having|whose)\b',sentence,re.I):continue
        for header in headers:
            groups={}
            for name in header:
                tokens=[t for t in re.split(r'[_\s-]+',name.strip().lower()) if t]
                statistic=STATISTICS.get(tokens[-1]) if tokens else None
                stem=' '.join(tokens[:-1]) if statistic else name
                groups.setdefault(stem,set())
                if statistic:groups[stem].add(statistic)
            absent=[(stem,sorted(wanted-stats)) for stem,stats in groups.items() if not wanted<=stats]
            if absent:
                witnesses='; '.join(stem+' lacks '+','.join(missing) for stem,missing in absent[:3])
                findings.append('Current description universally attributes a conjunctive statistic set '+','.join(sorted(wanted))+' to each variable/field/column, but its complete own cited header has '+str(len(absent))+' name groups without that full set: '+witnesses+'. Presence of these suffixes in different columns does not support their conjunction for every variable. Repair the scope/assertion through the same named contract while retaining supported table/field content; do not supply unstated statistics or discard the useful description.')
    return findings
def issues(claims,local,name_context):
    result={}
    for key,item in claims.items():
        c=item['claim'];refs=c.get('refs',[])
        if c.get('status')=='unknown' or not refs:continue
        units=[local.get(r['source_id']) for r in refs]
        if any(u is None or not nonsemantic(u) for u in units):continue
        name=name_context.get(key,{}).get('original_source_name','')
        # Name tokens can legitimately express a process; raw values alone
        # cannot. Do not accept another column name from a whole header.
        name=name.replace('_',' ')
        problems=quantified_statistics(c['value'],units)
        for label,pattern in PROCESS:
            words=[m.group(0) for m in pattern.finditer(c['value'])]
            if words and not pattern.search(name):
                problems.append('Original description adds '+label+' wording '+repr(words)+'. Its own cited sources contain only names/types/file metadata or sample cells, not a production-process definition for this field. Non-contradiction and numeric observations do not establish this assertion. Repair the unsupported wording while retaining useful source-backed naming; do not discard the entire description.')
        for label,words in source_process_phrases(c['value'],name):
            problems.append('Original description adds '+label+' wording '+repr(words)+'. Its own cited names, statistic suffixes, file-name/type metadata or sample cells do not establish an actual data-origin relation or actual production/aggregation process. Original Dataset rule9 distinguishes named statistics/row spacing from evidence of aggregation. Remove or correct the actual unsupported assertion, or bind applicable positive original method/provenance evidence. Keep supported header/count/naming content; changing inferred status or deleting only a lead-in word is insufficient. No replacement description is supplied.')
        if problems:result[key]=problems
    return result
