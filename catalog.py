
"""Discover every UCC laptop and find same-SKU product pages at retailers."""
import json, re, time
from urllib.parse import urljoin, urlparse, urlencode
import requests
from bs4 import BeautifulSoup
from monitor import HEADERS, norm, check_site

UCC_CATEGORY = "https://www.ucc.com.bd/laptops"
SITES = {
    "Ryans": "https://www.ryans.com/search?keyword={q}",
    "Star Tech": "https://www.startech.com.bd/index.php?route=product/search&search={q}",
    "Techland": "https://www.techlandbd.com/search?search={q}",
}
TIMEOUT=(12,25)
KNOWN_URLS = {
    "UN35MSI02B": {
        "Ryans": "https://www.ryans.com/acer-aspire-15-as15-42-amd-ryzen-5-7430u-15.6-inch-fhd-laptop",
        "Star Tech": "https://www.startech.com.bd/acer-aspire-15-as15-42-r5-ryzen-5-15-inch-laptop",
        "Techland": "https://www.techlandbd.com/acer-aspire-15-as15-42-ryzen-5-7430u-156-inch-fhd-ips-laptop",
    }
}
CARD_SELECTORS=(".product-thumb",".product-layout",".product-item",".product-card",
                ".product-grid .item",".product-list .item","[class*='product-item']")

def clean(s): return re.sub(r"\s+"," ",str(s or "")).strip()

def get(session,url):
    r=session.get(url,headers=HEADERS,timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def product_links(html, page_url, host="ucc.com.bd"):
    soup=BeautifulSoup(html,"html.parser")
    cards=[]
    for sel in CARD_SELECTORS:
        cards=soup.select(sel)
        if cards: break
    anchors=[]
    if cards:
        for card in cards: anchors.extend(card.select("a[href]"))
    else: anchors=soup.select("a[href]")
    found=set()
    for a in anchors:
        u=urljoin(page_url,a.get("href",""))
        p=urlparse(u); path=p.path.strip("/").lower()
        if p.netloc.lower().removeprefix("www.")!=host or not path: continue
        if any(x in path for x in ("category","brand","search","account","checkout","compare","wishlist","blog","news","contact","information")): continue
        if path in ("laptops","gaming-laptops","acer-brand-laptops"): continue
        if path.count("/")>1: continue
        found.add(u.split("#")[0])
    return sorted(found)

def extract_identity(html, url):
    soup=BeautifulSoup(html,"html.parser")
    h=soup.find("h1")
    title=clean(h.get_text(" ",strip=True) if h else (soup.title.get_text(" ",strip=True) if soup.title else url))
    text=clean(soup.get_text(" ",strip=True))
    sku=None
    patterns=[
      r"(?:SKU|MPN|Part\s*(?:No\.?|Number|#)|Product\s*Code)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._/-]{4,})",
      r"\b([A-Z]{2}\.[A-Z0-9]{2,12}\.[A-Z0-9]{2,12})\b"
    ]
    for pat in patterns:
        m=re.search(pat,text,re.I)
        if m: sku=m.group(1).strip("."); break
    return title,sku,text

def discover_ucc(session,max_pages=30):
    urls=set()
    for page in range(1,max_pages+1):
        url=UCC_CATEGORY if page==1 else UCC_CATEGORY+"?page="+str(page)
        html=get(session,url)
        batch=product_links(html,url)
        new=set(batch)-urls
        urls.update(batch)
        print(f"UCC page {page}: {len(batch)} links, {len(new)} new")
        if page>1 and not new: break
        time.sleep(.4)
    products=[]
    for url in sorted(urls):
        try:
            html=get(session,url)
            title,sku,_=extract_identity(html,url)
            if not title or title.lower() in ("laptops","products"): continue
            products.append({"id":sku or url,"name":title,"sku":sku,"urls":{"UCC":url}})
        except Exception as e:
            print("UCC detail skipped",url,str(e)[:100])
    # Deduplicate on normalized SKU, otherwise URL
    unique={}
    for p in products:
        k=norm(p.get("sku")) or p["urls"]["UCC"]
        unique.setdefault(k,p)
    return list(unique.values())

def candidate_links(html, base_url, host):
    soup=BeautifulSoup(html,"html.parser")
    anchors=[]
    for sel in CARD_SELECTORS:
        cards=soup.select(sel)
        if cards:
            for c in cards: anchors.extend(c.select("a[href]"))
            break
    if not anchors: anchors=soup.select("a[href]")
    out=[]
    for a in anchors:
        u=urljoin(base_url,a.get("href","")); p=urlparse(u)
        if p.netloc.lower().removeprefix("www.")!=host: continue
        path=p.path.strip("/")
        if not path or any(x in path.lower() for x in ("search","category","brand","account","checkout","cart","compare","wishlist","blog","news")): continue
        if path.count("/")>1: continue
        if u not in out: out.append(u)
    return out[:12]

def find_competitor(session,site,product):
    sku=product.get("sku")
    if not sku: return None
    host={"Ryans":"ryans.com","Star Tech":"startech.com.bd","Techland":"techlandbd.com"}[site]
    # SKU-only search is intentional: no guessed model-only matches.
    query=requests.utils.quote(sku)
    search_url=SITES[site].format(q=query)
    try:
        html=get(session,search_url)
    except Exception as e:
        print(site,"search failed",sku,str(e)[:100]); return None
    for url in candidate_links(html,search_url,host):
        try:
            result=check_site(site,url,norm(sku),session)
            if result["status"]=="OK" and result["sku_status"] in ("VERIFIED","PRESENT") and result["price"]:
                return url
        except Exception: pass
        time.sleep(.25)
    return None

def build_catalog():
    with requests.Session() as session:
        session.headers.update(HEADERS)
        products=discover_ucc(session)
        print("UCC laptop products discovered:",len(products))
        for i,p in enumerate(products,1):
            print(f"[{i}/{len(products)}] {p['name']} | {p.get('sku') or 'SKU MISSING'}")
            if p.get("sku"):
                sku_key=norm(p["sku"])
                for site in ("Ryans","Star Tech","Techland"):
                    match=KNOWN_URLS.get(sku_key,{}).get(site)
                    if match:
                        checked=check_site(site,match,sku_key,session)
                        if checked["status"]=="OK" and checked["sku_status"] in ("VERIFIED","PRESENT"):
                            p["urls"][site]=match
                            continue
                    match=find_competitor(session,site,p)
                    if match: p["urls"][site]=match
            time.sleep(.5)
    if not products: raise RuntimeError("No UCC laptop products discovered; refusing to overwrite products.json.")
    with open("products.json","w",encoding="utf-8") as f:
        json.dump(products,f,ensure_ascii=False,indent=2)
    print("Saved products.json with",len(products),"UCC products.")
    return products

if __name__=="__main__": build_catalog()
