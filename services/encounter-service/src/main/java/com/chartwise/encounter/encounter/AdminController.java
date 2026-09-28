package com.chartwise.encounter.encounter;

import com.chartwise.encounter.encounter.Dtos.AdminEncounterView;
import com.chartwise.encounter.retention.RetentionJob;
import com.chartwise.encounter.security.CurrentUser;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/admin")
public class AdminController {

    private final EncounterService service;
    private final RetentionJob retention;

    public AdminController(EncounterService service, RetentionJob retention) {
        this.service = service;
        this.retention = retention;
    }

    @GetMapping("/encounters")
    public List<AdminEncounterView> encounters(@RequestParam(required = false) EncounterStatus status,
                                               @RequestParam(defaultValue = "200") int limit) {
        return service.adminList(status, limit);
    }

    @PostMapping("/encounters/{id}/retry")
    public AdminEncounterView retry(@PathVariable UUID id) {
        return service.retry(id, CurrentUser.get());
    }

    @PostMapping("/retention/run")
    public RetentionJob.Result runRetention() {
        return retention.purge();
    }
}
