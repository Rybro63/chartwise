package com.chartwise.encounter.crypto;

import com.chartwise.encounter.config.ChartwiseProperties;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.stereotype.Component;

/**
 * AES-256-GCM encryption for PHI columns (transcripts, notes, patient names).
 *
 * <p>Layout: {@code [format version: 1 byte][IV: 12 bytes][ciphertext + GCM tag]}. The version byte
 * leaves room for key rotation: a future version 2 can name a different key without rewriting rows.
 */
@Component
public class FieldEncryptor {

    private static final byte FORMAT_V1 = 1;
    private static final int IV_BYTES = 12;
    private static final int TAG_BITS = 128;

    private final SecretKey key;
    private final SecureRandom random = new SecureRandom();

    public FieldEncryptor(ChartwiseProperties properties) {
        byte[] raw = Base64.getDecoder().decode(properties.crypto().key());
        if (raw.length != 32) {
            throw new IllegalStateException("chartwise.crypto.key must decode to exactly 32 bytes (AES-256)");
        }
        this.key = new SecretKeySpec(raw, "AES");
    }

    public byte[] encrypt(String plaintext) {
        if (plaintext == null) {
            return null;
        }
        try {
            byte[] iv = new byte[IV_BYTES];
            random.nextBytes(iv);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, iv));
            byte[] sealed = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
            return ByteBuffer.allocate(1 + IV_BYTES + sealed.length)
                    .put(FORMAT_V1)
                    .put(iv)
                    .put(sealed)
                    .array();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("encryption failed", e);
        }
    }

    public String decrypt(byte[] stored) {
        if (stored == null) {
            return null;
        }
        if (stored.length < 1 + IV_BYTES || stored[0] != FORMAT_V1) {
            throw new IllegalStateException("unrecognised ciphertext format");
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, stored, 1, IV_BYTES));
            byte[] plain = cipher.doFinal(stored, 1 + IV_BYTES, stored.length - 1 - IV_BYTES);
            return new String(plain, StandardCharsets.UTF_8);
        } catch (GeneralSecurityException e) {
            // Deliberately no detail: the message must never echo stored data.
            throw new IllegalStateException("decryption failed");
        }
    }
}
