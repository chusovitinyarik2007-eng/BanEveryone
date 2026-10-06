import json


def safejson_loads(data):
    try:
        return json.loads(str(data))
    except:
        return data