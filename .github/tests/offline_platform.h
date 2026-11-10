#import <Cocoa/Cocoa.h>
#import <ImageIO/ImageIO.h>
#import <CoreServices/CoreServices.h>

@interface FixtureApplication : NSObject
+ (id)currentApplication;
+ (NSArray *)runningApplicationsWithBundleIdentifier:(NSString *)identifier;
- (NSString *)localizedName;
- (NSString *)bundleIdentifier;
- (int)processIdentifier;
- (BOOL)terminate;
- (BOOL)forceTerminate;
- (BOOL)isActive;
- (BOOL)activateWithOptions:(NSUInteger)options;
- (id)icon;
- (BOOL)hide;
- (BOOL)unhide;
- (BOOL)isHidden;
- (NSURL *)bundleURL;
- (NSURL *)executableURL;
@end
@interface FixtureWorkspace : NSObject
+ (id)sharedWorkspace;
- (NSArray *)runningApplications;
@end
@interface FixtureBundle : NSObject
+ (id)bundleWithIdentifier:(NSString *)identifier;
- (NSURL *)bundleURL;
@end
@interface FixtureImage : NSObject
- (CGImageRef)CGImageForProposedRect:(NSRect *)rect context:(id)context hints:(id)hints;
@end

CFURLRef FixtureURLCreate(CFAllocatorRef, CFStringRef, CFURLPathStyle, Boolean);
CFStringRef FixtureURLCopyPath(CFURLRef, CFURLPathStyle);
CGImageDestinationRef FixtureDestinationCreate(CFMutableDataRef, CFStringRef, size_t, CFDictionaryRef);
void FixtureDestinationAdd(CGImageDestinationRef, CGImageRef, CFDictionaryRef);
bool FixtureDestinationFinalize(CGImageDestinationRef);

#define NSRunningApplication FixtureApplication
#define NSWorkspace FixtureWorkspace
#define NSBundle FixtureBundle
#define NSImage FixtureImage
#define CFURLCreateWithFileSystemPath FixtureURLCreate
#define CFURLCopyFileSystemPath FixtureURLCopyPath
#define CGImageDestinationCreateWithData FixtureDestinationCreate
#define CGImageDestinationAddImage FixtureDestinationAdd
#define CGImageDestinationFinalize FixtureDestinationFinalize
