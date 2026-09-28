package com.chartwise.encounter.crypto;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.chartwise.encounter.config.ChartwiseProperties;
import java.util.Base64;
import org.junit.jupiter.api.Test;

class FieldEncryptorTest {

    private final FieldEncryptor encryptor = new FieldEncryptor(new ChartwiseProperties(null,
            new ChartwiseProperties.Crypto(Base64.getEncoder().encodeToString(new byte[32])),
            null, null, null, null, null));

    @Test
    void roundTrips() {
        assertThat(encryptor.decrypt(encryptor.encrypt("synthetic transcript ✓"))).isEqualTo("synthetic transcript ✓");
    }

    @Test
    void usesAFreshIvEveryTime() {
        assertThat(encryptor.encrypt("same")).isNotEqualTo(encryptor.encrypt("same"));
    }

    @Test
    void detectsTampering() {
        byte[] sealed = encryptor.encrypt("synthetic");
        sealed[sealed.length - 1] ^= 1;
        assertThatThrownBy(() -> encryptor.decrypt(sealed)).hasMessage("decryption failed");
    }

    @Test
    void rejectsShortKeys() {
        assertThatThrownBy(() -> new FieldEncryptor(new ChartwiseProperties(null,
                new ChartwiseProperties.Crypto(Base64.getEncoder().encodeToString(new byte[16])),
                null, null, null, null, null)))
                .isInstanceOf(IllegalStateException.class);
    }
}
