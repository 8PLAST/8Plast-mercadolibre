"""Derived geometry with explicit assumptions; no stock or physical-load claims."""
import re
import unicodedata


def verified_geometry_answer(question, context):
    """Render verified numbers for a single-purpose bin-sizing request.

    The language model must not turn a correct calculation into a false formula.
    Mixed questions and follow-ups retain their model draft for human review.
    """
    text = unicodedata.normalize('NFKD', question.lower()).encode('ascii','ignore').decode()
    if (not re.search(r'que\s+(?:tamano|medida)\s+de\s+bolsa', text)
            or re.search(r'dialogo original|sobr|precio|stock|envio|entrega|micron|resist|certific|alimento|\bkg\b|\bkilo', text)
            or context.get('catalog',{}).get('warnings')):
        return None
    calc = next((e for e in context.get('reasoning',[]) if e['id']=='calculation:rectangular_bin'), None)
    if not calc or not calc['value']['recommended']:
        return None
    value=calc['value']; chosen=value['recommended']
    number=lambda x: format(x, 'g').replace('.',',')
    a,b=value['mouth_cm']
    answer=(f"¡Hola! Te recomendamos la bolsa de {number(chosen['width_cm'])}×{number(chosen['length_cm'])} cm. "
            f"La boca de tu tacho necesita al menos {number(a)} + {number(b)} = "
            f"{number(value['minimum_flat_width_cm'])} cm de ancho de bolsa aplanada, sin margen. ")
    tight=next((x for x in value['options'] if x['fit']=='ajustado'),None)
    ids=[calc['id'],chosen['evidence_id']]
    if tight:
        answer+=(f"La de {number(tight['width_cm'])}×{number(tight['length_cm'])} cm quedaría ajustada de boca; "
                 f"la de {number(chosen['width_cm'])} cm de ancho te da más holgura. ")
        ids.append(tight['evidence_id'])
    if chosen['length_cm']>value['estimated_length_cm']+10:
        answer+='De largo te va a sobrar y podés doblar el excedente sobre el borde. '
    return answer+'¡Saludos, 8Plast!',ids


def reasoning_context(question, alternatives, technical):
    text = unicodedata.normalize('NFKD', question.lower().replace('×','x')).encode('ascii', 'ignore').decode()
    if not re.search(r'\b(tacho|cesto|contenedor)\b', text):
        return []
    # Only derive from an explicit rectangular mouth and an explicit height.
    mouth = re.search(r'boca\s+rectangular\s+(?:de\s+)?(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*cm', text)
    height = re.search(r'(\d+(?:[.,]\d+)?)\s*cm\s+de\s+alto', text)
    if not mouth or not height:
        return []
    a, b = (float(v.replace(',', '.')) for v in mouth.groups())
    h = float(height[1].replace(',', '.'))
    if min(a, b, h) <= 0:
        return []
    width = a + b
    length = h + min(a, b) / 2 + 15
    family = None
    if technical.get('identity_confirmed'):
        family = technical['knowledge'][0]['value']['family']
    # Restrict to the known family; otherwise describe consorcio alternatives
    # without claiming that the queried MLA has been identified.
    families = {family} if family else {'consorcio_black', 'consorcio_green'}
    options = []
    for entry in alternatives:
        value = entry['value']
        if value['family'] not in families or value.get('kind') != 'bags':
            continue
        w, l = value['width_cm'], value['length_cm']
        if w >= width and l >= length:
            options.append({'evidence_id': entry['id'], 'width_cm': w, 'length_cm': l,
                            'width_margin_cm': round(w-width, 2),
                            'fit': 'holgado' if w-width >= 5 else 'ajustado'})
    options.sort(key=lambda x: (x['width_cm'], x['length_cm']))
    return [{'id': 'calculation:rectangular_bin', 'authority': 'derived_geometry',
             'value': {'mouth_cm': [a, b], 'height_cm': h,
                       'minimum_flat_width_cm': width,
                       'estimated_length_cm': length,
                       'assumptions': 'Fondo similar a la boca; largo orientativo = altura + mitad del lado menor + 15 cm para doblar/cerrar. Holgura de ancho de 5 cm orientativa, no tolerancia de fabricación.',
                       'options': options,
                       'recommended': next((x for x in options if x['fit']=='holgado'), None)}}]
