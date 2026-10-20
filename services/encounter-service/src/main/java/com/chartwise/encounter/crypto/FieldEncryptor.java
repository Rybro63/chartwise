package com.chartwise.encounter.crypto;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * AES-256-GCM encryption for PHI columns (transcripts, notes, patient names), with envelope
 * encryption: fields are encrypted with a data key, and the data key is stored beside them wrapped
 * by a key-encryption key from a {@link DataKeySource} (AWS KMS in a deployment).
 *
 * <p>Formats, told apart by the first byte:
 * <ul>
 *   <li>v2 (written now): {@code [2][wrapped key length: 2 bytes][wrapped data key][IV: 12][ciphertext + tag]}.
 *       The header is authenticated as associated data, so a wrapped key can't be swapped.</li>
 *   <li>v1 (read only): {@code [1][IV: 12][ciphertext + tag]} under the legacy key, for rows
 *       written before envelope encryption.</li>
 * </ul>
 *
 * <p>A data key is reused for {@code maxUses} fields or {@code maxAge}, whichever comes first, so
 * the key source (a KMS call) is not on every write; unwrapped keys are cached for reads.
 */
public class FieldEncryptor {

    private static final byte FORMAT_V1 = 1;
    private static final byte FORMAT_V2 = 2;
    private static final int IV_BYTES = 12;
    private static final int TAG_BITS = 128;
    private static final int CACHED_KEYS = 1_000;

    private final DataKeySource keys;
    private final SecretKey legacyKey;
    private final long maxUses;
    private final Duration maxAge;
    private final Clock clock;
    private final SecureRandom random = new SecureRandom();
    private final Map<ByteBuffer, SecretKey> unwrapped = Collections.synchronizedMap(
            new LinkedHashMap<>(16, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<ByteBuffer, SecretKey> eldest) {
                    return size() > CACHED_KEYS;
                }
            });

    private Current current;

    private record Current(SecretKey key, byte[] wrapped, Instant created, long[] uses) {}

    public FieldEncryptor(DataKeySource keys, SecretKey legacyKey, long maxUses, Duration maxAge, Clock clock) {
        this.keys = keys;
        this.legacyKey = legacyKey;
        this.maxUses = maxUses;
        this.maxAge = maxAge;
        this.clock = clock;
    }

    /** A 32-byte AES key from base64 configuration. */
    public static SecretKey aesKey(String base64) {
        byte[] raw = java.util.Base64.getDecoder().decode(base64);
        if (raw.length != 32) {
            throw new IllegalStateException("chartwise.crypto.key must decode to exactly 32 bytes (AES-256)");
        }
        return new SecretKeySpec(raw, "AES");
    }

    public byte[] encrypt(String plaintext) {
        return plaintext == null ? null : encryptBytes(plaintext.getBytes(StandardCharsets.UTF_8));
    }

    public String decrypt(byte[] stored) {
        byte[] plain = decryptBytes(stored);
        return plain == null ? null : new String(plain, StandardCharsets.UTF_8);
    }

    public byte[] encryptBytes(byte[] plaintext) {
        if (plaintext == null) {
            return null;
        }
        Current key = dataKey();
        try {
            byte[] header = ByteBuffer.allocate(3 + key.wrapped().length)
                    .put(FORMAT_V2).putShort((short) key.wrapped().length).put(key.wrapped()).array();
            byte[] iv = new byte[IV_BYTES];
            random.nextBytes(iv);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key.key(), new GCMParameterSpec(TAG_BITS, iv));
            cipher.updateAAD(header);
            byte[] sealed = cipher.doFinal(plaintext);
            return ByteBuffer.allocate(header.length + IV_BYTES + sealed.length).put(header).put(iv).put(sealed).array();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("encryption failed", e);
        }
    }

    public byte[] decryptBytes(byte[] stored) {
        if (stored == null) {
            return null;
        }
        if (stored.length > 0 && stored[0] == FORMAT_V1) {
            return decryptV1(stored);
        }
        if (stored.length < 3 || stored[0] != FORMAT_V2) {
            throw new IllegalStateException("unrecognised ciphertext format");
        }
        int wrappedLength = Short.toUnsignedInt(ByteBuffer.wrap(stored, 1, 2).getShort());
        int headerLength = 3 + wrappedLength;
        if (stored.length < headerLength + IV_BYTES) {
            throw new IllegalStateException("unrecognised ciphertext format");
        }
        ByteBuffer wrapped = ByteBuffer.wrap(stored, 3, wrappedLength).slice();
        try {
            SecretKey key = unwrapped.computeIfAbsent(copy(wrapped),
                    w -> new SecretKeySpec(keys.unwrap(w.array()), "AES"));
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(TAG_BITS, stored, headerLength, IV_BYTES));
            cipher.updateAAD(stored, 0, headerLength);
            return cipher.doFinal(stored, headerLength + IV_BYTES, stored.length - headerLength - IV_BYTES);
        } catch (GeneralSecurityException | RuntimeException e) {
            // Deliberately no detail: the message must never echo stored data.
            throw new IllegalStateException("decryption failed");
        }
    }

    private byte[] decryptV1(byte[] stored) {
        if (legacyKey == null || stored.length < 1 + IV_BYTES) {
            throw new IllegalStateException("decryption failed");
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, legacyKey, new GCMParameterSpec(TAG_BITS, stored, 1, IV_BYTES));
            return cipher.doFinal(stored, 1 + IV_BYTES, stored.length - 1 - IV_BYTES);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("decryption failed");
        }
    }

    private synchronized Current dataKey() {
        Instant now = clock.instant();
        if (current == null || current.uses()[0] >= maxUses || !now.isBefore(current.created().plus(maxAge))) {
            DataKeySource.DataKey fresh = keys.generate();
            SecretKey key = new SecretKeySpec(fresh.plaintext(), "AES");
            current = new Current(key, fresh.wrapped(), now, new long[] {0});
            unwrapped.put(ByteBuffer.wrap(fresh.wrapped().clone()), key);
        }
        current.uses()[0]++;
        return current;
    }

    private static ByteBuffer copy(ByteBuffer b) {
        byte[] out = new byte[b.remaining()];
        b.duplicate().get(out);
        return ByteBuffer.wrap(out);
    }
}
