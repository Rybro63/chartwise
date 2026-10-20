package com.chartwise.encounter.crypto;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.concurrent.atomic.AtomicInteger;
import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.junit.jupiter.api.Test;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.kms.model.DecryptRequest;
import software.amazon.awssdk.services.kms.model.DecryptResponse;
import software.amazon.awssdk.services.kms.model.GenerateDataKeyRequest;
import software.amazon.awssdk.services.kms.model.GenerateDataKeyResponse;

class FieldEncryptorTest {

    private static final SecretKey KEY = FieldEncryptor.aesKey(Base64.getEncoder().encodeToString(new byte[32]));

    /** Counts generate and unwrap calls, the way a KMS bill would. */
    static class CountingSource implements DataKeySource {
        final LocalDataKeySource local = new LocalDataKeySource(KEY);
        final AtomicInteger generated = new AtomicInteger();
        final AtomicInteger unwrapped = new AtomicInteger();

        @Override
        public DataKey generate() {
            generated.incrementAndGet();
            return local.generate();
        }

        @Override
        public byte[] unwrap(byte[] wrapped) {
            unwrapped.incrementAndGet();
            return local.unwrap(wrapped);
        }
    }

    private final MutableClock clock = new MutableClock();
    private final CountingSource source = new CountingSource();
    private final FieldEncryptor encryptor = new FieldEncryptor(source, KEY, 3, Duration.ofHours(1), clock);

    @Test
    void roundTripsInTheEnvelopeFormat() {
        byte[] sealed = encryptor.encrypt("synthetic transcript ✓");
        assertThat(sealed[0]).isEqualTo((byte) 2);
        assertThat(encryptor.decrypt(sealed)).isEqualTo("synthetic transcript ✓");
    }

    @Test
    void usesAFreshIvEveryTime() {
        assertThat(encryptor.encrypt("same")).isNotEqualTo(encryptor.encrypt("same"));
    }

    @Test
    void detectsTamperingWithTheCiphertext() {
        byte[] sealed = encryptor.encrypt("synthetic");
        sealed[sealed.length - 1] ^= 1;
        assertThatThrownBy(() -> encryptor.decrypt(sealed)).hasMessage("decryption failed");
    }

    @Test
    void detectsASwappedDataKey() {
        byte[] a = encryptor.encrypt("first");
        clock.advance(Duration.ofHours(2)); // forces a new data key
        byte[] b = encryptor.encrypt("second");
        // Splice b's wrapped key into a: the header is authenticated, so this must not decrypt.
        int keyLength = Short.toUnsignedInt(ByteBuffer.wrap(a, 1, 2).getShort());
        System.arraycopy(b, 3, a, 3, keyLength);
        assertThatThrownBy(() -> encryptor.decrypt(a)).hasMessage("decryption failed");
    }

    @Test
    void reusesADataKeyThenRotatesItByUseAndAge() {
        for (int i = 0; i < 3; i++) {
            encryptor.encrypt("field " + i);
        }
        assertThat(source.generated).hasValue(1);
        encryptor.encrypt("fourth use");
        assertThat(source.generated).hasValue(2);
        clock.advance(Duration.ofMinutes(61));
        encryptor.encrypt("an hour later");
        assertThat(source.generated).hasValue(3);
    }

    @Test
    void unwrapsEachStoredDataKeyOnceAndCachesIt() {
        byte[] one = encryptor.encrypt("one");
        byte[] two = encryptor.encrypt("two");
        FieldEncryptor reader = new FieldEncryptor(source, KEY, 3, Duration.ofHours(1), clock);
        assertThat(reader.decrypt(one)).isEqualTo("one");
        assertThat(reader.decrypt(two)).isEqualTo("two");
        assertThat(source.unwrapped).hasValue(1);
    }

    @Test
    void stillReadsRowsWrittenBeforeEnvelopeEncryption() throws Exception {
        byte[] iv = new byte[12];
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, KEY, new GCMParameterSpec(128, iv));
        byte[] sealed = cipher.doFinal("written in format v1".getBytes(StandardCharsets.UTF_8));
        byte[] v1 = ByteBuffer.allocate(1 + iv.length + sealed.length).put((byte) 1).put(iv).put(sealed).array();
        assertThat(encryptor.decrypt(v1)).isEqualTo("written in format v1");
    }

    @Test
    void rejectsShortKeys() {
        assertThatThrownBy(() -> FieldEncryptor.aesKey(Base64.getEncoder().encodeToString(new byte[16])))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void kmsSourceSendsTheKeyIdAndEncryptionContext() {
        StubKms kms = new StubKms();
        FieldEncryptor withKms = new FieldEncryptor(new KmsDataKeySource(kms, "alias/chartwise-phi"), null, 100,
                Duration.ofHours(1), clock);
        byte[] sealed = withKms.encrypt("synthetic");
        FieldEncryptor fresh = new FieldEncryptor(new KmsDataKeySource(kms, "alias/chartwise-phi"), null, 100,
                Duration.ofHours(1), clock);
        assertThat(fresh.decrypt(sealed)).isEqualTo("synthetic");
        assertThat(kms.generateCalls).hasValue(1);
        assertThat(kms.decryptCalls).hasValue(1);
    }

    /** Stands in for AWS KMS: "wraps" by reversing bytes, and checks every request is scoped. */
    static class StubKms implements KmsClient {
        final AtomicInteger generateCalls = new AtomicInteger();
        final AtomicInteger decryptCalls = new AtomicInteger();

        @Override
        public GenerateDataKeyResponse generateDataKey(GenerateDataKeyRequest request) {
            generateCalls.incrementAndGet();
            assertThat(request.keyId()).isEqualTo("alias/chartwise-phi");
            assertThat(request.encryptionContext()).isEqualTo(KmsDataKeySource.ENCRYPTION_CONTEXT);
            byte[] plaintext = new byte[32];
            new java.security.SecureRandom().nextBytes(plaintext);
            return GenerateDataKeyResponse.builder().plaintext(SdkBytes.fromByteArray(plaintext))
                    .ciphertextBlob(SdkBytes.fromByteArray(reverse(plaintext))).build();
        }

        @Override
        public DecryptResponse decrypt(DecryptRequest request) {
            decryptCalls.incrementAndGet();
            assertThat(request.encryptionContext()).isEqualTo(KmsDataKeySource.ENCRYPTION_CONTEXT);
            return DecryptResponse.builder()
                    .plaintext(SdkBytes.fromByteArray(reverse(request.ciphertextBlob().asByteArray()))).build();
        }

        private static byte[] reverse(byte[] in) {
            byte[] out = new byte[in.length];
            for (int i = 0; i < in.length; i++) {
                out[i] = in[in.length - 1 - i];
            }
            return out;
        }

        @Override
        public String serviceName() {
            return "kms";
        }

        @Override
        public void close() {}
    }

    static class MutableClock extends Clock {
        private Instant now = Instant.parse("2026-10-19T12:00:00Z");

        void advance(Duration d) {
            now = now.plus(d);
        }

        @Override
        public Instant instant() {
            return now;
        }

        @Override
        public java.time.ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(java.time.ZoneId zone) {
            return this;
        }
    }
}
