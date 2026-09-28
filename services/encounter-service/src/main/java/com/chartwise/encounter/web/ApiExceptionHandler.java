package com.chartwise.encounter.web;

import com.chartwise.encounter.encounter.InvalidStateException;
import com.chartwise.encounter.encounter.NotFoundException;
import com.chartwise.encounter.encounter.StaleVersionException;
import com.chartwise.encounter.fhir.FhirUnavailableException;
import jakarta.persistence.OptimisticLockException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.orm.ObjectOptimisticLockingFailureException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Maps domain errors to RFC 9457 problem responses. Messages are written by us and never include
 * request content, so they are safe to return and to log.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    @ExceptionHandler(NotFoundException.class)
    ProblemDetail notFound(NotFoundException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, e.getMessage());
    }

    @ExceptionHandler({InvalidStateException.class, StaleVersionException.class})
    ProblemDetail conflict(RuntimeException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, e.getMessage());
    }

    @ExceptionHandler({OptimisticLockException.class, ObjectOptimisticLockingFailureException.class,
            DataIntegrityViolationException.class})
    ProblemDetail concurrentUpdate(RuntimeException e) {
        return ProblemDetail.forStatusAndDetail(HttpStatus.CONFLICT, "the encounter was changed concurrently; reload and retry");
    }

    @ExceptionHandler(FhirUnavailableException.class)
    ProblemDetail fhirUnavailable(FhirUnavailableException e) {
        log.warn("FHIR call failed: {}", e.getMessage());
        return ProblemDetail.forStatusAndDetail(HttpStatus.SERVICE_UNAVAILABLE, "patient record server unavailable");
    }
}
