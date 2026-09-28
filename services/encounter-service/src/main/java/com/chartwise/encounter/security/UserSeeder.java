package com.chartwise.encounter.security;

import com.chartwise.encounter.config.ChartwiseProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/** Creates the demo accounts on first start. Disabled with SEED_USERS=false. */
@Component
public class UserSeeder implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(UserSeeder.class);

    private final AppUserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final ChartwiseProperties properties;

    public UserSeeder(AppUserRepository users, PasswordEncoder passwordEncoder, ChartwiseProperties properties) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.properties = properties;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        if (!properties.seed().enabled()) {
            return;
        }
        String password = properties.seed().password();
        seed("clinician", password, Role.CLINICIAN, "Dr. Demo Clinician");
        seed("scribe", password, Role.SCRIBE, "Demo Scribe");
        seed("admin", password, Role.ADMIN, "Demo Admin");
        seed("note-worker", properties.seed().workerPassword(), Role.SERVICE, "Note Worker");
    }

    private void seed(String username, String password, Role role, String displayName) {
        if (users.findByUsername(username).isEmpty()) {
            users.save(new AppUser(username, passwordEncoder.encode(password), role, displayName));
            log.info("Seeded user {} with role {}", username, role);
        }
    }
}
