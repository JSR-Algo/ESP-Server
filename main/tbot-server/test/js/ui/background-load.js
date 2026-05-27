// background imageLoadDetect
(function() {
    const backgroundContainer = document.getElementById('backgroundContainer');

    // ExtractbackgroundImageURL
    let bgImageUrl = window.getComputedStyle(backgroundContainer).backgroundImage;
    const urlMatch = bgImageUrl && bgImageUrl.match(/url\(["']?(.*?)["']?\)/);
    
    if (!urlMatch || !urlMatch[1]) {
        console.warn('not yetExtracttoValidbackground ofImageURL');
        return;
    }
    
    bgImageUrl = urlMatch[1];
    
    const bgImage = new Image();
    bgImage.onerror = function() {
        console.error('backgroundImageLoadFail:', bgImageUrl);
    };

    // LoadSuccessShowModelLoad
    bgImage.onload = function() {
        modelLoading.style.display = 'flex';
    };

    bgImage.src = bgImageUrl;
})();