import string
import random

def generate_password(length):
    chars=string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(chars) for _ in range(length))