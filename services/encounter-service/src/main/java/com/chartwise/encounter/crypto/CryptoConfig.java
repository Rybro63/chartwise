package com.chartwise.encounter.crypto;

import com.chartwise.encounter.config.ChartwiseProperties;
import java.time.Clock;
import java.time.Duration;
import java.util.Objects;
import javax.crypto.SecretKey;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import software.amazon.awssdk.services.kms.KmsClient;

/**
 * Chooses the data key source: AWS KMS when chartwise.crypto.kms-key-id is set (credentials and
 * region from the AWS default chain, e.g. EKS Pod Identity), otherwise the local ENCRYPTION_KEY.
 * ENCRYPTION_KEY also stays the key for reading rows written before envelope encryption.
 */
@Configuration
public class CryptoConfig {

    private static final Logger log = LoggerFactory.getLogger(CryptoConfig.class);

    @Bean
    FieldEncryptor fieldEncryptor(ChartwiseProperties properties) {
        ChartwiseProperties.Crypto crypto = properties.crypto();
        SecretKey legacy = crypto.key() == null || crypto.key().isBlank() ? null : FieldEncryptor.aesKey(crypto.key());
        DataKeySource source;
        if (crypto.kmsKeyId() != null && !crypto.kmsKeyId().isBlank()) {
            source = new KmsDataKeySource(KmsClient.create(), crypto.kmsKeyId());
            log.info("PHI field encryption: data keys from AWS KMS");
        } else {
            source = new LocalDataKeySource(Objects.requireNonNull(legacy, "chartwise.crypto.key is required without a KMS key"));
            log.info("PHI field encryption: data keys wrapped by the local key");
        }
        return new FieldEncryptor(source, legacy,
                Objects.requireNonNullElse(crypto.dataKeyMaxUses(), 100_000L),
                Objects.requireNonNullElse(crypto.dataKeyMaxAge(), Duration.ofHours(1)),
                Clock.systemUTC());
    }
}
