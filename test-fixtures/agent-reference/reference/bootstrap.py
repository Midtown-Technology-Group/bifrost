import features.cove

from bifrost import workflow


@workflow(id="81c8961e-cedb-4d51-831b-d29a75e99cb9", name="c1_r_reference_bootstrap")
async def c1_r_reference_bootstrap() -> dict:
    return {"fixture_only": True}
