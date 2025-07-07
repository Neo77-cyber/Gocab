import json
import datetime
import decimal
from uuid import UUID

class DjangoSafeJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)   # or float(obj) if needed
        elif isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        elif isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)
