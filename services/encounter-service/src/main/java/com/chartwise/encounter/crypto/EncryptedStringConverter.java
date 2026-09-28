package com.chartwise.encounter.crypto;

import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;
import org.springframework.stereotype.Component;

/** JPA converter that stores a String attribute as AES-GCM ciphertext. */
@Component
@Converter
public class EncryptedStringConverter implements AttributeConverter<String, byte[]> {

    private final FieldEncryptor encryptor;

    public EncryptedStringConverter(FieldEncryptor encryptor) {
        this.encryptor = encryptor;
    }

    @Override
    public byte[] convertToDatabaseColumn(String attribute) {
        return encryptor.encrypt(attribute);
    }

    @Override
    public String convertToEntityAttribute(byte[] dbData) {
        return encryptor.decrypt(dbData);
    }
}
