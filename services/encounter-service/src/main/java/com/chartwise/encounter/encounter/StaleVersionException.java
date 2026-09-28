package com.chartwise.encounter.encounter;

public class StaleVersionException extends RuntimeException {
    public StaleVersionException(String message) {
        super(message);
    }
}
