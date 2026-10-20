package com.chartwise.encounter.encounter;

/** A rejected upload. The message is ours and safe to return. */
public class InvalidUploadException extends RuntimeException {
    public InvalidUploadException(String message) {
        super(message);
    }
}
