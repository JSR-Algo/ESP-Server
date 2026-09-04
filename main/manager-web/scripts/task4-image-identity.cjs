'use strict';

function inspectAndPinCandidateImages(candidate, inspectImage) {
  const backendId = inspectImage(candidate.backendReference);
  if (backendId !== candidate.backendId) throw new Error('candidate backend image ID mismatch');
  const webId = inspectImage(candidate.webReference);
  if (webId !== candidate.webId) throw new Error('candidate web image ID mismatch');
  return { backendImage: backendId, webImage: webId };
}

function verifyStartedServiceImages(expected, inspectServiceImage) {
  for (const [service, imageId] of Object.entries(expected)) {
    if (inspectServiceImage(service) !== imageId) {
      throw new Error(`started ${service} container image ID mismatch`);
    }
  }
}

function verifyStartedServicePortBindings(expected, inspectServicePorts) {
  for (const [service, binding] of Object.entries(expected)) {
    const ports = inspectServicePorts(service);
    const observed = ports && ports[binding.containerPort];
    if (
      !Array.isArray(observed) || observed.length !== 1
      || observed[0].HostIp !== '127.0.0.1'
      || observed[0].HostPort !== binding.hostPort
    ) {
      throw new Error(`started ${service} container host port binding mismatch`);
    }
  }
}

module.exports = {
  inspectAndPinCandidateImages,
  verifyStartedServiceImages,
  verifyStartedServicePortBindings,
};
