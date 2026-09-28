package com.chartwise.encounter.security;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;

/** The authenticated caller, as recorded in audit events and note authorship. */
public record CurrentUser(String username, Role role) {

    public static final CurrentUser SYSTEM = new CurrentUser("system", Role.SERVICE);

    public static CurrentUser get() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null || !auth.isAuthenticated()) {
            throw new IllegalStateException("no authenticated user");
        }
        Role role = auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .filter(a -> a != null && a.startsWith("ROLE_"))
                .map(a -> Role.valueOf(a.substring("ROLE_".length())))
                .findFirst()
                .orElseThrow(() -> new IllegalStateException("user has no role"));
        return new CurrentUser(auth.getName(), role);
    }
}
