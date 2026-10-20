package com.chartwise.encounter.crypto;

/**
 * Where data keys come from (envelope encryption). Each data key encrypts many fields; it is stored
 * beside them only in wrapped form, encrypted by a key-encryption key that never leaves its source
 * (AWS KMS in a deployment, ENCRYPTION_KEY locally). Rotating or revoking the key-encryption key
 * therefore never requires rewriting every row by hand.
 */
public interface DataKeySource {

    /** A fresh 256-bit data key: the plaintext to encrypt with now, and the wrapped form to store. */
    DataKey generate();

    /** The plaintext of a stored wrapped data key. */
    byte[] unwrap(byte[] wrapped);

    record DataKey(byte[] plaintext, byte[] wrapped) {}
}
