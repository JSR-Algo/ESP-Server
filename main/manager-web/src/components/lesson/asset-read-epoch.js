let assetReadEpoch = 0;

export function reserveAssetReadEpoch() {
  assetReadEpoch += 1;
  return assetReadEpoch;
}
