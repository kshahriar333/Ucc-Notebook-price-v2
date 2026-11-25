"""SKU-first matching for UCC laptop price comparison."""
import re


def normalize(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def extract_cpu(text):
    patterns = [
        r"\bRyzen\s+AI\s+[579]\s+\d{3,4}[A-Z]{0,3}\b",
        r"\bRyzen\s+[3579]\s+\d{4,5}[A-Z]{0,3}\b",
        r"\bCore\s+Ultra\s+[3579]\s+\d{3}[A-Z]{0,3}\b",
        r"\bCore\s+i[3579]\s*[- ]?\s*\d{4,5}[A-Z]{0,3}\b",
        r"\bCore\s+[3579]\s+\d{3,5}[A-Z]{0,3}\b",
        r"\bProcessor\s+N\d{3}\b",
        r"\bApple\s+(M[1-5](?:\s+Pro|\s+Max|\s+Ultra)?)\b",
        r"\b(A18\s+Pro|A18|A17\s+Pro)\b",
        r"\b(Snapdragon\s+X\s+(?:Elite|Plus))\b",
    ]
    for pattern in patterns:
        found = re.search(pattern, str(text or ""), re.I)
        if found:
            return normalize(found.group(0))
    return None


def extract_model(text):
    patterns = [
        r"\bMacBook\s+(?:Air|Pro|Neo)\s+\d{1,2}(?:\.\d)?\s*-?\s*inch\b",
        r"\bMacBook\s+(?:Air|Pro|Neo)\b",
        r"\b(?:AS|AN|TL|TMP|PHN|PH|A\d{3})\s?[A-Z0-9]{1,10}(?:-[A-Z0-9]{1,10}){0,2}\b",
        r"\b([A-Z]\d[A-Z]{2,7}(?:-[A-Z0-9]{2,8})?)\b",
    ]
    for pattern in patterns:
        found = re.search(pattern, str(text or ""), re.I)
        if found:
            return normalize(found.group(0))
    return None


def extract_ram(text):
    for pattern in (
        r"\bRAM\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\bMemory\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\b(\d{1,2})\s*GB\s*(?:LPDDR[345X]\s*)?(?:RAM|Memory)\b",
        r"\b(\d{1,2})\s*GB\s*(?:LPDDR[345X]|DDR[345X])\b",
        r"\b(\d{1,2})\s*GB\s+Unified\s+Memory\b",
    ):
        found = re.search(pattern, str(text or ""), re.I)
        if found:
            return int(found.group(1))
    return None


def extract_ssd_gb(text):
    direct = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(TB|GB)\s*", str(text or ""), re.I)
    if direct:
        amount = float(direct.group(1))
        return int(amount * 1024) if direct.group(2).upper() == "TB" else int(amount)
    patterns = [
        (r"\b(\d+(?:\.\d+)?)\s*TB\b(?=.{0,60}\bSSD\b)", "TB"),
        (r"\bSSD\)?\s*[:|=-]?\s*(\d+(?:\.\d+)?)\s*(TB|GB)\b", None),
        (r"\b(?:Storage(?:\s+Capacity)?|Solid-State\s+Drive)\s*[:|=-]?\s*(\d{3,4})\s*GB\b", "GB"),
    ]
    for pattern, unit in patterns:
        found = re.search(pattern, str(text or ""), re.I)
        if found:
            amount = float(found.group(1))
            detected = unit or found.group(2).upper()
            return int(amount * 1024) if detected == "TB" else int(amount)
    return None


def product_text(product):
    return " ".join(str(product.get(key) or "") for key in (
        "name", "description", "page_text", "full_text", "model",
        "processor", "ram", "ddr", "ssd",
    ))


def value(product, key):
    direct = product.get(key)
    if direct not in (None, ""):
        raw = str(direct)
        if key == "model":
            return extract_model(raw) or normalize(raw)
        if key == "processor":
            return extract_cpu(raw) or normalize(raw)
        if key == "ram":
            found = re.search(r"\d{1,2}", raw)
            return extract_ram(raw) or (int(found.group(0)) if found else None)
        if key == "ssd":
            found = re.search(r"\d{3,4}", raw)
            return extract_ssd_gb(raw) or (int(found.group(0)) if found else None)
        if key == "ddr":
            found = re.search(r"(?:LPDDR[345X]|DDR[345X])", raw, re.I)
            return found.group(0).upper() if found else normalize(raw)
    text = product_text(product)
    if key == "model":
        return extract_model(text)
    if key == "processor":
        return extract_cpu(text)
    if key == "ram":
        return extract_ram(text)
    if key == "ssd":
        return extract_ssd_gb(text)
    if key == "ddr":
        found = re.search(r"\b(?:LPDDR[345X]|DDR[345X])\b", text, re.I)
        return found.group(0).upper() if found else None
    return None


def compare_products(ucc, competitor):
    ucc_sku = normalize(ucc.get("sku"))
    other_sku = normalize(competitor.get("sku"))
    exact_sku = bool(ucc_sku and other_sku and ucc_sku == other_sku)

    checks = {}
    for key in ("model", "processor", "ram", "ddr", "ssd"):
        left, right = value(ucc, key), value(competitor, key)
        if left is None or right is None:
            checks[key] = "UNKNOWN"
        else:
            checks[key] = "MATCH" if left == right else "MISMATCH"

    mismatches = [key for key, result in checks.items() if result == "MISMATCH"]
    unknowns = [key for key, result in checks.items() if result == "UNKNOWN"]
    matched = [key for key, result in checks.items() if result == "MATCH"]

    # SKU is the primary identifier, but an explicit spec conflict always wins.
    # If SKU matches and no specification conflicts, unknown fields are tolerated.
    # If SKU differs/is missing, require model + CPU + at least one memory/storage
    # match, with no known mismatch.
    if mismatches:
        allowed = False
        status = "CONFIGURATION MISMATCH"
    elif exact_sku:
        allowed = True
        status = "EXACT SKU MATCH"
    else:
        enough_evidence = (
            checks["model"] == "MATCH"
            and checks["processor"] == "MATCH"
            and any(checks[key] == "MATCH" for key in ("ram", "ddr", "ssd"))
        )
        allowed = enough_evidence
        status = "SPECIFICATION MATCH" if allowed else "UNVERIFIED - INSUFFICIENT SPECS"

    return {
        "status": status,
        "sku_match": exact_sku,
        "checks": checks,
        "price_comparison_allowed": allowed,
        "mismatches": mismatches,
        "unknowns": unknowns,
        "matched_specs": matched,
    }
