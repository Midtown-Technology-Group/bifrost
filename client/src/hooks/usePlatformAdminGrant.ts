import { useAuth } from "@/contexts/AuthContext";

const PLATFORM_ADMIN_ROLES = ["Platform Admin", "Platform Owner"] as const;

export function usePlatformAdminGrant(): boolean {
	const { isPlatformAdmin, hasRole } = useAuth();
	return (
		isPlatformAdmin || PLATFORM_ADMIN_ROLES.some((role) => hasRole(role))
	);
}
