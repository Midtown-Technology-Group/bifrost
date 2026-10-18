import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
	useBridgeGrants,
	useUpdateBridgeGrants,
} from "@/hooks/useBridgeGrants";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

type BridgePermission =
	components["schemas"]["RoleBridgeGrantsUpdate"]["permissions"][number];
type BridgeUpdate = components["schemas"]["RoleBridgeGrantsUpdate"];
const choices: {
	permission: BridgePermission;
	label: string;
	description: string;
}[] = [
	{
		permission: "devices.readwrite",
		label: "Manage devices",
		description:
			"Registry, enrollment, key metadata and revocation. Usable key issue or rotation also requires Execute devices in the same organization.",
	},
	{
		permission: "devices.execute",
		label: "Execute devices",
		description:
			"Create, cancel and read device jobs, logs and script bodies.",
	},
	{
		permission: "agents.promote",
		label: "Promote own agents",
		description:
			"Promote private agents owned by the holder in their home organization; ordinary callers cannot promote Solution-managed agents.",
	},
];

/** Mounted only for a Platform Admin, separately from delegated identity editing. */
export function BridgeGrantsPanel({ roleId }: { roleId: string }) {
	const query = useBridgeGrants(roleId);
	const update = useUpdateBridgeGrants();
	const [draft, setDraft] = useState<BridgeUpdate | null>(null);
	const [error, setError] = useState<string | null>(null);
	const data = query.data;
	const held = draft?.permissions ?? data?.permissions ?? [];
	const dirty =
		!!data &&
		[...held].sort().join() !== [...data.permissions].sort().join();
	return (
		<section
			aria-labelledby="bridge-grants-title"
			className="space-y-3 rounded-lg border p-4"
		>
			<h2 id="bridge-grants-title" className="font-semibold">
				Device and agent grants
			</h2>
			<p className="text-sm text-muted-foreground">
				Privileged grants managed by Platform Admins. Ordinary holders
				receive these grants only in their home organization.
			</p>
			{(query.isError || error) && (
				<Alert variant="destructive">
					<AlertDescription>
						{error ??
							getErrorMessage(
								query.error,
								"Bridge grants could not be loaded.",
							)}
					</AlertDescription>
				</Alert>
			)}
			{!data ? (
				<Button
					variant="outline"
					disabled={query.isFetching}
					onClick={() => void query.refetch()}
				>
					Reload grants
				</Button>
			) : (
				<>
					{choices.map(({ permission, label, description }) => (
						<div
							key={permission}
							className="flex items-start gap-3"
						>
							<Checkbox
								id={`bridge-${permission}`}
								checked={held.includes(permission)}
								disabled={
									data.is_builtin ||
									query.isFetching ||
									update.isPending
								}
								onCheckedChange={(checked) => {
									setDraft({
										permissions:
											checked === true
												? [...held, permission]
												: held.filter(
														(p) => p !== permission,
													),
										expected_permissions:
											draft?.expected_permissions ??
											data.permissions,
										expected_version:
											draft?.expected_version ??
											data.version,
									});
									setError(null);
								}}
							/>
							<div>
								<Label htmlFor={`bridge-${permission}`}>
									{label}
								</Label>
								<p className="text-sm text-muted-foreground">
									{description}
								</p>
							</div>
						</div>
					))}
					<div className="flex gap-2">
						<Button
							disabled={
								!dirty || query.isFetching || update.isPending
							}
							onClick={() => {
								if (!draft) return;
								update.mutate(
									{
										params: { path: { role_id: roleId } },
										body: draft,
									},
									{
										onSuccess: () => {
											setDraft(null);
											setError(null);
										},
										onError: (failure) => {
											setError(
												getErrorMessage(
													failure,
													"Grants were not saved. Reload the current grants before trying again.",
												),
											);
										},
									},
								);
							}}
						>
							Save device and agent grants
						</Button>
						<Button
							variant="outline"
							disabled={update.isPending}
							onClick={() => {
								setDraft(null);
								setError(null);
								void query.refetch();
							}}
						>
							Reload grants
						</Button>
					</div>
				</>
			)}
		</section>
	);
}
