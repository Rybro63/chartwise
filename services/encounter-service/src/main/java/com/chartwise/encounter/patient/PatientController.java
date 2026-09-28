package com.chartwise.encounter.patient;

import com.chartwise.encounter.fhir.FhirGateway;
import com.chartwise.encounter.fhir.PatientSummary;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PatientController {

    private final FhirGateway fhir;

    public PatientController(FhirGateway fhir) {
        this.fhir = fhir;
    }

    @GetMapping("/api/patients")
    public List<PatientSummary> search(@RequestParam(required = false) String name,
                                       @RequestParam(defaultValue = "50") int count) {
        return fhir.searchPatients(name, Math.min(Math.max(count, 1), 200));
    }
}
