package com.chartwise.encounter.crypto;

import java.util.Map;
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.services.kms.KmsClient;
import software.amazon.awssdk.services.kms.model.DataKeySpec;
import software.amazon.awssdk.services.kms.model.GenerateDataKeyResponse;

/**
 * Data keys from AWS KMS. The key-encryption key never leaves KMS: the service asks KMS for a new
 * data key (plaintext plus a wrapped copy) and later asks KMS to unwrap stored copies. Every call
 * carries an encryption context, so a wrapped key only unwraps for this purpose, and each one is
 * recorded in CloudTrail.
 */
public class KmsDataKeySource implements DataKeySource {

    static final Map<String, String> ENCRYPTION_CONTEXT = Map.of("service", "chartwise-encounter", "purpose", "phi-fields");

    private final KmsClient kms;
    private final String keyId;

    public KmsDataKeySource(KmsClient kms, String keyId) {
        this.kms = kms;
        this.keyId = keyId;
    }

    @Override
    public DataKey generate() {
        GenerateDataKeyResponse response = kms.generateDataKey(r -> r
                .keyId(keyId)
                .keySpec(DataKeySpec.AES_256)
                .encryptionContext(ENCRYPTION_CONTEXT));
        return new DataKey(response.plaintext().asByteArray(), response.ciphertextBlob().asByteArray());
    }

    @Override
    public byte[] unwrap(byte[] wrapped) {
        return kms.decrypt(r -> r
                .keyId(keyId)
                .ciphertextBlob(SdkBytes.fromByteArray(wrapped))
                .encryptionContext(ENCRYPTION_CONTEXT)).plaintext().asByteArray();
    }
}
