from app.models.schemas import ConnectivityResult, ContainerInfo
from app.services import connectivity_tests


async def test_proxy_connectivity(container: ContainerInfo) -> ConnectivityResult:
    if not container.dashboard.testable or not container.lan_address:
        return ConnectivityResult(
            service=container.name,
            success=False,
            error="Not testable or no LAN address",
            tested_via="n/a",
        )

    test_fn = connectivity_tests.get_test(container.dashboard.protocol)
    if test_fn is None:
        return ConnectivityResult(
            service=container.name,
            success=False,
            error=f"Unsupported protocol: {container.dashboard.protocol}",
            tested_via="n/a",
        )
    return await test_fn(container)
