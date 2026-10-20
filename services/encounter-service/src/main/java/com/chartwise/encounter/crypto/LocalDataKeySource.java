package com.chartwise.encounter.crypto;

import java.nio.ByteBuffer;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Wraps data keys with a local key (ENCRYPTION_KEY), for development and tests. */
public class LocalDataKeySource implements DataKeySource {

    private static final int IV_BYTES = 12;
    private final SecretKey keyEncryptionKey;
    private final SecureRandom random = new SecureRandom();

    public LocalDataKeySource(SecretKey keyEncryptionKey) {
        this.keyEncryptionKey = keyEncryptionKey;
    }

    @Override
    public DataKey generate() {
        byte[] plaintext = new byte[32];
        random.nextBytes(plaintext);
        try {
            byte[] iv = new byte[IV_BYTES];
            random.nextBytes(iv);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, keyEncryptionKey, new GCMParameterSpec(128, iv));
            byte[] sealed = cipher.doFinal(plaintext);
            return new DataKey(plaintext, ByteBuffer.allocate(IV_BYTES + sealed.length).put(iv).put(sealed).array());
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("data key generation failed", e);
        }
    }

    @Override
    public byte[] unwrap(byte[] wrapped) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, keyEncryptionKey, new GCMParameterSpec(128, wrapped, 0, IV_BYTES));
            return cipher.doFinal(wrapped, IV_BYTES, wrapped.length - IV_BYTES);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("data key unwrap failed");
        }
    }
}
