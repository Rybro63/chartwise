package com.chartwise.encounter.fhir;

import ca.uhn.fhir.context.FhirContext;
import ca.uhn.fhir.rest.api.MethodOutcome;
import ca.uhn.fhir.rest.client.api.IGenericClient;
import ca.uhn.fhir.rest.client.api.ServerValidationModeEnum;
import ca.uhn.fhir.rest.server.exceptions.BaseServerResponseException;
import ca.uhn.fhir.rest.server.exceptions.ResourceGoneException;
import ca.uhn.fhir.rest.server.exceptions.ResourceNotFoundException;
import com.chartwise.encounter.config.ChartwiseProperties;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.Period;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.Set;
import java.util.function.Function;
import org.hl7.fhir.instance.model.api.IBaseResource;
import org.hl7.fhir.r4.model.AllergyIntolerance;
import org.hl7.fhir.r4.model.Attachment;
import org.hl7.fhir.r4.model.Bundle;
import org.hl7.fhir.r4.model.CodeableConcept;
import org.hl7.fhir.r4.model.Condition;
import org.hl7.fhir.r4.model.DocumentReference;
import org.hl7.fhir.r4.model.Enumerations;
import org.hl7.fhir.r4.model.HumanName;
import org.hl7.fhir.r4.model.MedicationRequest;
import org.hl7.fhir.r4.model.Patient;
import org.hl7.fhir.r4.model.Reference;
import org.springframework.stereotype.Component;

@Component
public class HapiFhirGateway implements FhirGateway {

    static final String NOTE_IDENTIFIER_SYSTEM = "urn:chartwise:encounter-note";

    /**
     * Synthea records social determinants and care-process findings as Conditions. They are not
     * clinical problems and only add noise to a SOAP note prompt.
     */
    private static final List<String> NON_CLINICAL_CONDITION_MARKERS = List.of(
            "(situation)", "employment", "education", "labor force", "housing", "social", "violence",
            "criminal record", "transport", "stress (finding)", "medication review due", "refugee",
            "military service", "risk activity involvement", "unhealthy alcohol");

    private final IGenericClient client;

    public HapiFhirGateway(ChartwiseProperties properties) {
        FhirContext ctx = FhirContext.forR4();
        ctx.getRestfulClientFactory().setServerValidationMode(ServerValidationModeEnum.NEVER);
        ctx.getRestfulClientFactory().setConnectTimeout((int) properties.fhir().connectTimeout().toMillis());
        ctx.getRestfulClientFactory().setSocketTimeout((int) properties.fhir().socketTimeout().toMillis());
        this.client = ctx.newRestfulGenericClient(properties.fhir().baseUrl());
    }

    @Override
    public List<PatientSummary> searchPatients(String name, int count) {
        return call(() -> {
            var query = client.search().forResource(Patient.class);
            if (name != null && !name.isBlank()) {
                query.where(Patient.NAME.matches().value(name));
            }
            Bundle bundle = query.sort().ascending(Patient.FAMILY).count(count).returnBundle(Bundle.class).execute();
            return resources(bundle, Patient.class).stream().map(this::summary).toList();
        });
    }

    @Override
    public Optional<PatientSummary> findPatient(String patientId) {
        return call(() -> {
            try {
                Patient patient = client.read().resource(Patient.class).withId(patientId).execute();
                return Optional.of(summary(patient));
            } catch (ResourceNotFoundException | ResourceGoneException e) {
                return Optional.empty();
            }
        });
    }

    @Override
    public PatientContext loadContext(String patientId) {
        return call(() -> {
            Patient patient = client.read().resource(Patient.class).withId(patientId).execute();

            Bundle conditions = client.search().forResource(Condition.class)
                    .where(Condition.PATIENT.hasId(patientId))
                    .and(Condition.CLINICAL_STATUS.exactly().code("active"))
                    .count(200)
                    .returnBundle(Bundle.class)
                    .execute();
            Bundle medications = client.search().forResource(MedicationRequest.class)
                    .where(MedicationRequest.PATIENT.hasId(patientId))
                    .and(MedicationRequest.STATUS.exactly().code("active"))
                    .count(200)
                    .returnBundle(Bundle.class)
                    .execute();
            Bundle allergies = client.search().forResource(AllergyIntolerance.class)
                    .where(AllergyIntolerance.PATIENT.hasId(patientId))
                    .count(200)
                    .returnBundle(Bundle.class)
                    .execute();

            List<String> conditionNames = distinct(resources(conditions, Condition.class), c -> display(c.getCode()))
                    .stream()
                    .filter(HapiFhirGateway::isClinical)
                    .toList();
            List<String> medicationNames = distinct(resources(medications, MedicationRequest.class),
                    m -> m.hasMedicationCodeableConcept() ? display(m.getMedicationCodeableConcept()) : null);
            List<String> allergyNames = distinct(resources(allergies, AllergyIntolerance.class), a -> display(a.getCode()));

            return new PatientContext(age(patient), gender(patient), conditionNames, medicationNames, allergyNames);
        });
    }

    @Override
    public String fileNote(FiledNote note) {
        return call(() -> {
            DocumentReference doc = new DocumentReference();
            doc.addIdentifier().setSystem(NOTE_IDENTIFIER_SYSTEM).setValue(note.encounterId().toString());
            doc.setStatus(Enumerations.DocumentReferenceStatus.CURRENT);
            doc.setDocStatus(DocumentReference.ReferredDocumentStatus.FINAL);
            doc.getType().addCoding()
                    .setSystem("http://loinc.org")
                    .setCode("11506-3")
                    .setDisplay("Progress note");
            doc.addCategory().addCoding()
                    .setSystem("http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category")
                    .setCode("clinical-note")
                    .setDisplay("Clinical Note");
            doc.setSubject(new Reference("Patient/" + note.patientId()));
            doc.setDate(Date.from(note.approvedAt()));
            doc.setAuthenticator(new Reference().setDisplay(note.approvedBy()));
            doc.setDescription("SOAP note drafted by Chartwise and approved by a clinician");
            doc.addContent().setAttachment(new Attachment()
                    .setContentType("text/plain; charset=utf-8")
                    .setLanguage("en-US")
                    .setTitle("SOAP note")
                    .setData(note.text().getBytes(StandardCharsets.UTF_8))
                    .setCreation(Date.from(note.approvedAt())));

            // Conditional create: if a DocumentReference with this identifier already exists (for
            // example, the previous attempt succeeded but we crashed before recording it), the
            // server returns the existing one instead of creating a duplicate.
            MethodOutcome outcome = client.create()
                    .resource(doc)
                    .conditionalByUrl("DocumentReference?identifier=" + NOTE_IDENTIFIER_SYSTEM + "|" + note.encounterId())
                    .execute();
            return outcome.getId().getIdPart();
        });
    }

    private <T> T call(java.util.function.Supplier<T> action) {
        try {
            return action.get();
        } catch (ResourceNotFoundException e) {
            throw e;
        } catch (BaseServerResponseException e) {
            // Status only: FHIR error bodies can echo resource content.
            throw new FhirUnavailableException("FHIR server returned HTTP " + e.getStatusCode(), null);
        } catch (RuntimeException e) {
            throw new FhirUnavailableException("FHIR server unreachable: " + e.getClass().getSimpleName(), null);
        }
    }

    private PatientSummary summary(Patient p) {
        return new PatientSummary(
                p.getIdElement().getIdPart(),
                name(p),
                gender(p),
                p.hasBirthDate() ? p.getBirthDateElement().getValueAsString() : null);
    }

    private static String name(Patient p) {
        if (!p.hasName()) {
            return "(unnamed)";
        }
        HumanName n = p.getNameFirstRep();
        String given = String.join(" ", n.getGiven().stream().map(Object::toString).toList());
        return (given + " " + (n.hasFamily() ? n.getFamily() : "")).trim();
    }

    private static String gender(Patient p) {
        return p.hasGender() ? p.getGender().toCode() : "unknown";
    }

    private static Integer age(Patient p) {
        if (!p.hasBirthDate()) {
            return null;
        }
        LocalDate birth = p.getBirthDate().toInstant().atZone(ZoneId.of("UTC")).toLocalDate();
        LocalDate end = p.hasDeceasedDateTimeType()
                ? p.getDeceasedDateTimeType().getValue().toInstant().atZone(ZoneId.of("UTC")).toLocalDate()
                : LocalDate.now(ZoneId.of("UTC"));
        return Period.between(birth, end).getYears();
    }

    private static String display(CodeableConcept concept) {
        if (concept == null) {
            return null;
        }
        if (concept.hasText()) {
            return concept.getText();
        }
        return concept.getCoding().stream()
                .filter(c -> c.hasDisplay())
                .map(c -> c.getDisplay())
                .findFirst()
                .orElse(null);
    }

    private static boolean isClinical(String condition) {
        String lower = condition.toLowerCase(Locale.ROOT);
        return NON_CLINICAL_CONDITION_MARKERS.stream().noneMatch(lower::contains);
    }

    private static <R extends IBaseResource> List<R> resources(Bundle bundle, Class<R> type) {
        List<R> out = new ArrayList<>();
        for (Bundle.BundleEntryComponent entry : bundle.getEntry()) {
            if (type.isInstance(entry.getResource())) {
                out.add(type.cast(entry.getResource()));
            }
        }
        return out;
    }

    private static <R> List<String> distinct(List<R> items, Function<R, String> label) {
        Set<String> seen = new LinkedHashSet<>();
        for (R item : items) {
            String value = label.apply(item);
            if (value != null && !value.isBlank()) {
                seen.add(value.trim());
            }
        }
        return List.copyOf(seen);
    }
}
