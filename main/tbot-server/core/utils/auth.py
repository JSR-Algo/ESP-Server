import jwt
import time
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import base64


class AuthToken:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()  # Convert to bytes
        # fromKeyDerive fixed-length encryptionKey (32Byte for AES-256)
        self.encryption_key = self._derive_key(32)

    def _derive_key(self, length: int) -> bytes:
        """Derive fixed-length key"""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        # Use fixed salt (production should use random salt)
        salt = b"fixed_salt_placeholder"  # Production environment should use random generation
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            iterations=100000,
            backend=default_backend(),
        )
        return kdf.derive(self.secret_key)

    def _encrypt_payload(self, payload: dict) -> str:
        """Encrypt entire payload with AES-GCM"""
        # willpayloadConvert toJSONString
        payload_json = json.dumps(payload)

        # Generate RandomIV
        iv = os.urandom(12)
        # Create encryptor
        cipher = Cipher(
            algorithms.AES(self.encryption_key),
            modes.GCM(iv),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()

        # Encrypt and generate tag
        ciphertext = encryptor.update(payload_json.encode()) + encryptor.finalize()
        tag = encryptor.tag

        # Combine IV + Ciphertext + Tag
        encrypted_data = iv + ciphertext + tag
        return base64.urlsafe_b64encode(encrypted_data).decode()

    def _decrypt_payload(self, encrypted_data: str) -> dict:
        """Decrypt AES-GCM encrypted payload"""
        # DecodeBase64
        data = base64.urlsafe_b64decode(encrypted_data.encode())
        # Split Component
        iv = data[:12]
        tag = data[-16:]
        ciphertext = data[12:-16]

        # Create decryptor
        cipher = Cipher(
            algorithms.AES(self.encryption_key),
            modes.GCM(iv, tag),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()

        # Decrypt
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        return json.loads(plaintext.decode())

    def generate_token(self, device_id: str) -> str:
        """
        GenerateJWT token
        :param device_id: Device ID
        :return: JWT tokenString
        """
        # SetExpiration timefor1Hours later
        expire_time = datetime.now(timezone.utc) + timedelta(hours=1)

        # Create Originalpayload
        payload = {"device_id": device_id, "exp": expire_time.timestamp()}

        # Encrypt Entirepayload
        encrypted_payload = self._encrypt_payload(payload)

        # Create OuterpayloadContains encrypted data
        outer_payload = {"data": encrypted_payload}

        # UseJWTEncode
        token = jwt.encode(outer_payload, self.secret_key, algorithm="HS256")
        return token

    def verify_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """
        Validate token
        :param token: JWT token string
        :return: (valid, device ID)
        """
        try:
            # Verify outer firstJWT(signature andExpiration time)
            outer_payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])

            # Decrypt Innerpayload
            inner_payload = self._decrypt_payload(outer_payload["data"])

            # Check AgainExpiration time(double validation)
            if inner_payload["exp"] < time.time():
                return False, None

            return True, inner_payload["device_id"]

        except jwt.InvalidTokenError:
            return False, None
        except json.JSONDecodeError:
            return False, None
        except Exception as e:  # Catch other possibleError
            print(f"Token verification failed: {str(e)}")
            return False, None
