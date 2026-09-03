def normalize_path(path):
    return path.strip().rstrip("/")

def encoded_hex_checksum(data):
    return bytes(data).hex()

def retry_delay(attempt):
    return min(2 ** attempt, 60)
