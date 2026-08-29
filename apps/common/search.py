from django.db.models import Q


def phone_search_variants(query):
    cleaned = (query or "").strip()
    if not cleaned:
        return set()

    compact = "".join(cleaned.split())
    variants = {cleaned, compact}
    digits = "".join(character for character in compact if character.isdigit())
    if digits:
        variants.add(digits)
        if compact.startswith("+"):
            variants.add(f"+{digits}")
        if digits.startswith("20"):
            variants.add(f"+{digits}")
            if len(digits) > 2:
                variants.add(f"0{digits[2:]}")
        if digits.startswith("0") and len(digits) >= 10:
            variants.add(f"+20{digits[1:]}")
            variants.add(f"20{digits[1:]}")
    return {variant for variant in variants if variant}


def icontains_any_q(field_name, values):
    query = Q()
    for value in values:
        query |= Q(**{f"{field_name}__icontains": value})
    return query


def customer_phone_search_q(field_name, query):
    return icontains_any_q(field_name, phone_search_variants(query))
