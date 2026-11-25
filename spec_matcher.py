import re
from dataclasses import dataclass, asdict
from typing import Optional

def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

def first(patterns, text):
    for pattern in patterns:
        m=re.search(pattern,text,re.I)
        if m: return re.sub(r"\\s+"," ",m.group(1)).strip()
    return None

@dataclass
class LaptopIdentity:
    brand: Optional[str]=None
    series: Optional[str]=None
    model: Optional[str]=None
    processor: Optional[str]=None
    ram_gb: Optional[int]=None
    ram_type: Optional[str]=None
    storage_gb: Optional[int]=None
    storage_type: Optional[str]=None
    sku: Optional[str]=None
    def to_dict(self): return asdict(self)

def extract_identity(title, page_text):
    text=re.sub(r"\\s+"," ",f"{title or ''} {page_text or ''}").strip()
    u=text.upper()
    brand="Acer" if re.search(r"\\bACER\\b",u) else None
    series=first([
      r"\\b(ASPIRE\\s*[A-Z]?\\s*\\d{1,2})\\b",
      r"\\b(NITRO\\s*[A-Z]?\\s*\\d{1,2})\\b",
      r"\\b(SWIFT\\s*(?:GO|EDGE|X)?\\s*\\d{1,2})\\b",
      r"\\b(TRAVELMATE\\s*[A-Z]?\\s*\\d{1,2})\\b",
      r"\\b(EXTENSA\\s*\\d{1,2})\\b",
      r"\\b(PREDATOR\\s*(?:HELIOS|TRITON)\\s*(?:NEO\\s*)?\\d{1,2})\\b",
      r"\\b(CHROMEBOOK\\s*\\d{1,2})\\b"],u)
    model=first([r"\\b([A-Z]{2,4}\\d{2,3}[- ][A-Z0-9]{2,12})\\b",r"\\b([A-Z]{2,4}\\d{2,3})\\b"],u)
    cpu=first([
      r"\\b(INTEL\\s+CORE\\s+ULTRA\\s+[3579]\\s+\\d{3,5}[A-Z]{0,2})\\b",
      r"\\b(CORE\\s+ULTRA\\s+[3579]\\s+\\d{3,5}[A-Z]{0,2})\\b",
      r"\\b(INTEL\\s+CORE\\s+i[3579][ -]?\\d{3,5}[A-Z]{0,2})\\b",
      r"\\b(CORE\\s+i[3579][ -]?\\d{3,5}[A-Z]{0,2})\\b",
      r"\\b(AMD\\s+RYZEN\\s+[3579]\\s+\\d{4,5}[A-Z]{0,2})\\b",
      r"\\b(RYZEN\\s+[3579]\\s+\\d{4,5}[A-Z]{0,2})\\b"],u)
    if cpu: cpu=re.sub(r"\\s+"," ",cpu).replace("INTEL ","").replace("AMD ","")
    rm=re.search(r"\\b(\\d{1,3})\\s*GB\\s*(DDR[345]|LPDDR[345X]?)\\b",u)
    if not rm: rm=re.search(r"\\b(\\d{1,3})\\s*GB\\s+(?:RAM|MEMORY)\\b",u)
    ram=int(rm.group(1)) if rm else None
    rtype=rm.group(2) if rm and rm.lastindex and rm.lastindex>=2 else None
    sm=re.search(r"\\b(\\d{3,4})\\s*GB\\s*(NVME\\s*)?(SSD|HDD)\\b",u)
    if sm: storage=int(sm.group(1)); stype="SSD" if sm.group(3)=="SSD" or sm.group(2) else "HDD"
    else:
      tm=re.search(r"\\b([12])\\s*TB\\s*(NVME\\s*)?(SSD|HDD)\\b",u)
      storage=int(tm.group(1))*1024 if tm else None
      stype=("SSD" if tm.group(3)=="SSD" or tm.group(2) else "HDD") if tm else None
    sku=first([r"\\b(?:SKU|MPN|PART\\s*(?:NO\\.?|NUMBER|#)|PRODUCT\\s*CODE)\\s*[:#-]?\\s*([A-Z0-9][A-Z0-9._/-]{4,})",r"\\b([A-Z]{2}\\.[A-Z0-9]{2,12}\\.[A-Z0-9]{2,12})\\b"],text)
    return LaptopIdentity(brand,series,model,cpu,ram,rtype,storage,stype,sku)

def compare_laptops(a,b):
    sa,sb=normalize(a.sku),normalize(b.sku)
    if sa and sb:
      return {"matched":sa==sb,"method":"EXACT_SKU" if sa==sb else "SKU_CONFLICT","reasons":["SKU exact" if sa==sb else "SKU differs"]}
    required=("brand","series","processor","ram_gb","storage_gb","storage_type")
    missing=[k for k in required if getattr(a,k) in (None,"") or getattr(b,k) in (None,"")]
    if missing: return {"matched":False,"method":"INSUFFICIENT_SPECS","reasons":["Missing: "+", ".join(missing)]}
    diffs=[]
    for k in required:
      x,y=getattr(a,k),getattr(b,k)
      if normalize(x)!=normalize(y): diffs.append(f"{k}: {x} != {y}")
    if a.ram_type and b.ram_type and a.ram_type!=b.ram_type: diffs.append(f"ram_type: {a.ram_type} != {b.ram_type}")
    if a.model and b.model and normalize(a.model)!=normalize(b.model): diffs.append(f"model: {a.model} != {b.model}")
    if diffs: return {"matched":False,"method":"SPEC_CONFLICT","reasons":diffs}
    return {"matched":True,"method":"SPEC_MATCH","reasons":["Acer series/model, processor, RAM and storage match"]}
