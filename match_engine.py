"""Conservative laptop matching: compare prices only after core specs match."""
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
    ]
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), re.I)
        if match:
            return normalize(match.group(0))
    return None


def extract_model(text):
    text = str(text or "")
    patterns = [
        r"\b(?:AS|AN|TL|TMP|PHN|PH|A\d{3})\s?[A-Z0-9]{1,10}(?:-[A-Z0-9]{1,10}){0,2}\b",
        r"\b([A-Z]\d[A-Z]{2,7}(?:-[A-Z0-9]{2,8})?)\b",
        r"\b(B\d{2}[A-Z]{2,6}|A\d[A-Z]{3,6}|C\d[A-Z]{3,6}|F\d[A-Z]{2,5})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return normalize(match.group(0))
    return None


def extract_ram(text):
    patterns = [
        r"\bRAM\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\bMemory\s*[:\-]?\s*(\d{1,2})\s*GB\b",
        r"\b(\d{1,2})\s*GB\s*(?:DDR[345X]\s*)?(?:RAM|Memory)\b",
        r"\b(\d{1,2})\s*GB\s*DDR[345X]\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), re.I)
        if match:
            return int(match.group(1))
    return None


def extract_ssd_gb(text):
    text = str(text or "")
    patterns = [
        (r"\b(\d+(?:\.\d+)?)\s*TB\b(?=.{0,60}\bSSD\b)", "TB"),
        (r"\b(\d{3,4})\s*GB\b(?=.{0,60}\bSSD\b)(?![\s\S]{0,35}\b(?:RAM|DDR[345X])\b)", "GB"),
        (r"\bSSD\)?\s*[:|=-]?\s*(\d+(?:\.\d+)?)\s*(TB|GB)\b", None),
        (r"\b(?:Storage(?:\s+Capacity)?|Solid-State\s+Drive)\s*[:|=-]?\s*(\d{3,4})\s*GB\b", "GB"),
    ]
    for pattern, unit in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = float(match.group(1))
            detected_unit = unit or match.group(2).upper()
            return int(value * 1024) if detected_unit == "TB" else int(value)
    return None


def product_text(product):
    return " ".join(str(product.get(key) or "") for key in (
        "name", "description", "page_text", "full_text", "model",
        "processor", "ram", "ddr", "ssd",
    ))


def value(product, key):
    direct = product.get(key)
    if direct not in (None, ""):
        if key == "model":
            return extract_model(str(direct)) or normalize(direct)
        if key == "processor":
            return extract_cpu(str(direct)) or normalize(direct)
        if key == "ram":
            return extract_ram(str(direct)) or (
                int(re.search(r"\d{1,2}", str(direct)).group(0))
                if re.search(r"\d{1,2}", str(direct)) else None
            )
        if key == "ssd":
            return extract_ssd_gb(str(direct)) or (
                int(re.search(r"\d{3,4}", str(direct)).group(0))
                if re.search(r"\d{3,4}", str(direct)) else None
            )
        if key == "ddr":
            match = re.search(r"(?:LPDDR[345X]|DDR[345X])", str(direct), re.I)
            return match.group(0).upper() if match else normalize(direct)
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
        match = re.search(r"\b(?:LPDDR[345X]|DDR[345X])\b", text, re.I)
        return match.group(0).upper() if match else None
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
    price_allowed = not mismatches and not unknowns
    if mismatches:
        status = "CONFIGURATION MISMATCH"
    elif unknowns:
        status = "UNVERIFIED - MISSING SPECS"
    elif exact_sku:
        status = "EXACT SKU + SPEC MATCH"
    else:
        status = "SPECIFICATION MATCH (SKU DIFFERENT/NOT FOUND)"

    return {
        "status": status,
        "sku_match": exact_sku,
        "checks": checks,
        "price_comparison_allowed": price_allowed,
        "mismatches": mismatches,
        "unknowns": unknowns,
    }
