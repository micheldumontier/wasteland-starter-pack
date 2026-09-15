"""Credential verification: RFC 8032 vectors, and a real Camelot-signed credential.

The credential below was fetched from the live Camelot registrar on 2026-09-15 and
is pinned verbatim. It is public data. If this test fails, either the verification
code or Camelot's signing construction has changed.
"""

import base64
import copy
import unittest

from examples import camelot_trust, ed25519
from wasteland.protocol import canonical

# RFC 8032 section 7.1 test vectors: (secret, public, message, signature)
RFC_8032 = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a3"
        "3bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15"
        "996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16"
        "f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]

CAMELOT_ISSUER = "https://w3id.org/academic-wasteland/camelot/issuers/ethics-council"
CAMELOT_PUBLIC_KEY = "ed25519:seGCer9_zlMqtNycVI19l-gW1ppLlR8k_sR4E7CSiI8"
CAMELOT_CREDENTIAL = {
    "@context": [
        "https://www.w3.org/ns/credentials/v2",
        "https://w3id.org/academic-wasteland/credentials/v0.1"
    ],
    "credentialStatus": {
        "id": "https://w3id.org/academic-wasteland/camelot/status/6dd70cbe-6972-49ea-a4cd-d6be4656de1d",
        "type": "RegistrarStatus"
    },
    "credentialSubject": {
        "id": "https://w3id.org/academic-wasteland/camelot/issuers/ubar-dac",
        "publicKey": "ed25519:1m6S8xbIuEACd6-dbBG8Gq5cUJ3Wfc6jbENz_adOS7o",
        "roles": [
            "DataAccessCommittee"
        ]
    },
    "id": "https://w3id.org/academic-wasteland/camelot/credentials/6dd70cbe-6972-49ea-a4cd-d6be4656de1d",
    "issuer": "https://w3id.org/academic-wasteland/camelot/issuers/ethics-council",
    "proof": {
        "created": "2026-09-14T08:22:00Z",
        "proofValue": "JuL0ozzZPacnivNDWWBHiZhI6NyHCuVkS5etfajmWdtQIij6XjqEZXLmmJFyxlTykWaND2G1w-MoGPYxnGnIAQ",
        "type": "PangenomeTownEd25519Jcs2026",
        "verificationMethod": "https://w3id.org/academic-wasteland/camelot/issuers/ethics-council#key-1"
    },
    "type": [
        "VerifiableCredential",
        "Accreditation"
    ],
    "validFrom": "2026-09-14T08:22:00Z",
    "validUntil": "2027-09-14T08:22:00Z"
}


def _b64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Ed25519Tests(unittest.TestCase):
    def test_rfc_8032_vectors(self):
        for secret, public, message, signature in RFC_8032:
            with self.subTest(message=message or "(empty)"):
                secret, message = bytes.fromhex(secret), bytes.fromhex(message)
                public, signature = bytes.fromhex(public), bytes.fromhex(signature)
                self.assertEqual(ed25519.public_key(secret), public)
                self.assertEqual(ed25519.sign(secret, message), signature)
                self.assertTrue(ed25519.verify(signature, message, public))

    def test_a_signature_does_not_verify_for_a_different_message(self):
        _, public, _, signature = RFC_8032[1]
        self.assertFalse(
            ed25519.verify(bytes.fromhex(signature), b"other", bytes.fromhex(public))
        )

    def test_every_bit_of_the_signature_matters(self):
        _, public, message, signature = RFC_8032[2]
        public, message = bytes.fromhex(public), bytes.fromhex(message)
        for index in (0, 31, 32, 63):
            flipped = bytearray(bytes.fromhex(signature))
            flipped[index] ^= 0x01
            with self.subTest(byte=index):
                self.assertFalse(ed25519.verify(bytes(flipped), message, public))

    def test_malformed_input_is_rejected_rather_than_raising(self):
        for signature, message, key in (
            (b"", b"m", bytes(32)),
            (bytes(64), b"m", b""),
            ("not-bytes", b"m", bytes(32)),
            (bytes(64), b"m", bytes(32)),
        ):
            self.assertFalse(ed25519.verify(signature, message, key))


class RealCamelotCredentialTests(unittest.TestCase):
    """Pinned live data: this is the construction Camelot actually signs."""

    def setUp(self):
        self.record = {
            "fetched": "2026-09-15T08:00:00+00:00",
            "source": "camelot via relay (pinned fixture)",
            "issuers": [{"id": CAMELOT_ISSUER, "publicKey": CAMELOT_PUBLIC_KEY}],
        }

    def test_a_live_camelot_credential_verifies(self):
        verdict = camelot_trust.verify_credential(
            copy.deepcopy(CAMELOT_CREDENTIAL), record=self.record
        )
        self.assertTrue(verdict["verified"])
        self.assertEqual(verdict["proof_type"], camelot_trust.PROOF_TYPE)
        self.assertEqual(verdict["issuer"], CAMELOT_ISSUER)

    def test_the_signed_payload_is_the_canonical_document_without_the_proof(self):
        credential = copy.deepcopy(CAMELOT_CREDENTIAL)
        signature = _b64(credential["proof"]["proofValue"])
        key = _b64(CAMELOT_PUBLIC_KEY.split(":", 1)[1])
        document = {k: v for k, v in credential.items() if k != "proof"}
        self.assertTrue(ed25519.verify(signature, canonical(document).encode(), key))

    def test_altering_any_claim_breaks_verification(self):
        for field, value in (
            ("credentialSubject", {"id": "https://example.invalid/me", "roles": ["Admin"]}),
            ("validFrom", "1999-01-01T00:00:00Z"),
            ("id", "https://w3id.org/academic-wasteland/camelot/credentials/forged"),
        ):
            credential = copy.deepcopy(CAMELOT_CREDENTIAL)
            credential[field] = value
            with self.subTest(field=field):
                with self.assertRaises(camelot_trust.TrustError):
                    camelot_trust.verify_credential(credential, record=self.record)

    def test_an_unknown_issuer_is_refused(self):
        with self.assertRaises(camelot_trust.TrustError):
            camelot_trust.verify_credential(
                copy.deepcopy(CAMELOT_CREDENTIAL),
                record={"fetched": "x", "source": "x", "issuers": []},
            )


if __name__ == "__main__":
    unittest.main()
