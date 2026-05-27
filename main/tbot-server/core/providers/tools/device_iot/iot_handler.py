"""IoT device support module, provides IoT device descriptors and status handling"""

import asyncio
from config.logger import setup_logging
from .iot_descriptor import IotDescriptor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


async def handleIotDescriptors(conn: "ConnectionHandler", descriptors):
    """Handle IoT description"""
    wait_max_time = 5
    while (
        not hasattr(conn, "func_handler")
        or conn.func_handler is None
        or not conn.func_handler.finish_init
    ):
        await asyncio.sleep(1)
        wait_max_time -= 1
        if wait_max_time <= 0:
            logger.bind(tag=TAG).debug("Connection object has no func_handler")
            return

    functions_changed = False

    for descriptor in descriptors:
        # IfdescriptorNonepropertiesandmethods,skip directly
        if "properties" not in descriptor and "methods" not in descriptor:
            continue

        # Handle MissingpropertiesCase of
        if "properties" not in descriptor:
            descriptor["properties"] = {}
            # frommethodsExtract all parameters asproperties
            if "methods" in descriptor:
                for method_name, method_info in descriptor["methods"].items():
                    if "parameters" in method_info:
                        for param_name, param_info in method_info["parameters"].items():
                            # ParameterInfoConvert to propertyInfo
                            descriptor["properties"][param_name] = {
                                "description": param_info["description"],
                                "type": param_info["type"],
                            }

        # CreateIOTDeviceDescriptionsymbol
        iot_descriptor = IotDescriptor(
            descriptor["name"],
            descriptor["description"],
            descriptor["properties"],
            descriptor["methods"],
        )
        conn.iot_descriptors[descriptor["name"]] = iot_descriptor
        functions_changed = True

    # IfRegisterNew function added, updatefunctionDescriptionList
    if functions_changed and hasattr(conn, "func_handler"):
        # Register IoT tooltoUnified tool handler
        await conn.func_handler.register_iot_tools(descriptors)

        conn.func_handler.current_support_functions()


async def handleIotStatus(conn: "ConnectionHandler", states):
    """Handle IoT status"""
    for state in states:
        for key, value in conn.iot_descriptors.items():
            if key == state["name"]:
                for property_item in value.properties:
                    for k, v in state["state"].items():
                        if property_item["name"] == k:
                            if type(v) != type(property_item["value"]):
                                logger.bind(tag=TAG).error(
                                    f"Property{property_item['name']}ofValue typeMismatch"
                                )
                                break
                            else:
                                property_item["value"] = v
                                logger.bind(tag=TAG).info(
                                    f"IoTStatusUpdate: {key} , {property_item['name']} = {v}"
                                )
                            break
                break
