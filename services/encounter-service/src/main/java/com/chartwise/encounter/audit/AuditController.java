package com.chartwise.encounter.audit;

import com.chartwise.encounter.security.CurrentUser;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AuditController {

    private final AuditLog auditLog;

    public AuditController(AuditLog auditLog) {
        this.auditLog = auditLog;
    }

    @GetMapping("/api/audit")
    @Transactional
    public List<AuditLog.AuditEntry> list(@RequestParam(required = false) UUID encounterId,
                                          @RequestParam(defaultValue = "200") int limit) {
        // Reading the audit log is itself audited.
        auditLog.record(CurrentUser.get(), AuditAction.AUDIT_VIEWED, encounterId, null,
                encounterId == null ? Map.of("scope", "all") : Map.of("scope", "encounter"));
        return auditLog.find(encounterId, Math.min(Math.max(limit, 1), 1000));
    }
}
