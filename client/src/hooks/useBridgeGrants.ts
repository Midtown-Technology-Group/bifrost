import { useQueryClient } from "@tanstack/react-query";
import { $api } from "@/lib/api-client";
import { invalidateAuthorization } from "@/lib/authorization";
import { USER_ACCESS_QUERY_KEY } from "@/services/access";

export function useBridgeGrants(roleId: string) {
	return $api.useQuery("get", "/api/roles/{role_id}/bridge-grants", {
		params: { path: { role_id: roleId } },
	});
}

export function useUpdateBridgeGrants() {
	const client = useQueryClient();
	return $api.useMutation("put", "/api/roles/{role_id}/bridge-grants", {
		onSuccess: () => {
			for (const path of [
				"/api/roles",
				"/api/roles/{role_id}/bridge-grants",
				"/api/roles/{role_id}/permissions",
				"/api/users",
				"/api/users/{user_id}/role-assignments",
			]) {
				void client.invalidateQueries({ queryKey: ["get", path] });
			}
			void client.invalidateQueries({ queryKey: USER_ACCESS_QUERY_KEY });
			void invalidateAuthorization(client);
		},
	});
}
