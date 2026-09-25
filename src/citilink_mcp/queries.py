"""Our own minimal GraphQL documents for https://www.citilink.ru/graphql/.

Every field here was requested from the live server while building this
package (introspection is disabled there, and the client-side schema map in
the site's JS bundle lists fields the server rejects). The site's own queries
are not copied: they carry dozens of fields we do not need, including author
names in reviews.

Each document asks for ``viewer.currentCity``: the city the answer was
computed for. The client compares it with the city it asked for.
"""
from __future__ import annotations

VIEWER = "viewer{... on B2C_User{currentCity{... on Stores_City{id,name,spaceId,region}}}}"

PRICE = "price{current,old,club,clubPriceViewType,bonusPoints,sbpBonus,discount{percent}}"

# Fields shared by search results, batches and markdown listings.
SNIPPET = (
    "id,name,shortName,slug,isAvailable,isDiscount,originalProductId,multiplicity,searchDescription,"
    "isPaymentMandatory," + PRICE + ",rating,counters{opinions},brand{name},category{id,name},"
    "labels{type,title},stock{countInStores}"
)

PICKUP_DAYS = "self{availabilityByDays{deliveryTime,storeCount}}"
COURIER = "courier{variants(input:{filter:{types:[FAST,TODAY,STANDARD]}}){type,price,deliveryTime}}"

# The product's own rating ("Этот вариант товара" tab); `rating` next to it is the group's.
OWN_RATING = (
    "own:opinions(input:{pagination:{page:1,perPage:1},withGroup:false}){... on UGC_OpinionsPageConnection{"
    "payload{summary{rating},totalOpinionsForProduct}}}"
)

PROPERTIES = "propertiesGroup{name,properties{name,value,measure}}"

LISTING_ERROR = (
    "error{__typename,... on CatalogFilter_ProductsFilterIncorrectArgumentsError{message},"
    "... on CatalogFilter_ProductsFilterInternalError{message}}"
)

PAGE_INFO = "pageInfo{page,perPage,totalItems,totalPages,hasNextPage}"
SORTINGS = "sortings{slug,directions{id,isSelected}}"

SEARCH = (
    "query Search($in:CatalogFilter_FullSearchFilterInput!){" + VIEWER + ","
    "fullSearchFilter(filter:$in){record{"
    "products{" + SNIPPET + ",delivery{" + PICKUP_DAYS + "}," + OWN_RATING + "},"
    + PAGE_INFO + "," + SORTINGS + ","
    "categories{category{id,name,slug},productsCount,isSelected},"
    "groups{id,filter{__typename,... on CatalogFilter_ListFilter{filters{id,isSelected,total}},"
    "... on CatalogFilter_RangeFilter{fromValue,toValue,minValue,maxValue}}}},"
    + LISTING_ERROR + "}}"
)

PRODUCT = (
    "query Product($id:String!,$stores:Boolean!){" + VIEWER + ","
    "product(filter:{id:$id}){"
    "id,name,shortName,slug,isAvailable,isDiscount,originalProductId,searchDescription,multiplicity,"
    "isPaymentMandatory,isPreorderVAD,"
    "price{current,old,club,clubPriceViewType,isFairPrice,bonusPoints,sbpBonus,discount{percent}},"
    "rating,counters{opinions,discussions,discountedProducts},"
    "brand{name},category{id,name,slug},stock{countInStores},"
    "variants{name,variants{__typename,"
    "... on Catalog_ProductVariantLabel{label,product{id,isAvailable}},"
    "... on Catalog_ProductVariantColor{label,product{id,isAvailable}}}},"
    + PROPERTIES + ","
    "labels{type,title,description},"
    "actions(input:{limit:10}){items{id,type{type,name},shortDescription,disclaimer}},"
    "credit{credit{name,period,payment},installment{name,period,payment}},"
    "delivery{" + COURIER + ","
    "self{availabilityByDays{deliveryTime,storeCount},"
    "own:availabilityInStores(filter:{filters:[{id:\"full-store\",isSelected:true},{id:\"mini-store\",isSelected:true}]})"
    "@include(if:$stores){deliveries{deliveryTime,stores{items{availableCount,deliveryPrice,"
    "storeShort{id,name,address,format}}}}},"
    "partner:availabilityInStores(filter:{filters:[{id:\"outsource-store\",isSelected:true}]})"
    "@include(if:$stores){deliveries{deliveryTime,storeCount,stores{total,items{deliveryPrice}}}}}},"
    "own:opinions(input:{pagination:{page:1,perPage:1},withGroup:false}){... on UGC_OpinionsPageConnection{"
    "payload{summary{rating,ratingCounters{rating,count}},totalOpinionsForProduct,haveGroup}}},"
    "group:opinions(input:{pagination:{page:1,perPage:1},withGroup:true}){... on UGC_OpinionsPageConnection{"
    "payload{summary{rating,ratingCounters{rating,count}}},pageInfo{totalItems}}},"
    "summary{__typename,... on UGC_ProductOpinionSummary{rating,totalOpinions,productAISummary{text,positive,negative}}}"
    "}}"
)

BATCH = (
    "query Batch($ids:[String!]!){" + VIEWER + ","
    "products(filter:{ids:$ids}){" + SNIPPET + ",delivery{" + PICKUP_DAYS + "," + COURIER + "}," + OWN_RATING + "}}"
)

_OPINIONS_SUMMARY = "payload{summary{rating,ratingCounters{rating,count}},totalOpinionsForProduct,haveGroup}"

REVIEWS = (
    "query Reviews($id:String!,$in:UGC_OpinionsInput!){" + VIEWER + ","
    "product(filter:{id:$id}){id,shortName,slug,"
    "sel:opinions(input:$in){__typename,... on UGC_OpinionsPageConnection{"
    "payload{items{id,displayedDate,rating,pros,cons,text,isBest,source{sourceType,description},"
    "product{id,shortName},voteInfo{info{counters{likes,dislikes}}}}}," + PAGE_INFO + "}},"
    "own:opinions(input:{pagination:{page:1,perPage:1},withGroup:false}){... on UGC_OpinionsPageConnection{"
    + _OPINIONS_SUMMARY + "}},"
    "group:opinions(input:{pagination:{page:1,perPage:1},withGroup:true}){... on UGC_OpinionsPageConnection{"
    + _OPINIONS_SUMMARY + ",pageInfo{totalItems}}}"
    "}}"
)

QUESTIONS = (
    "query Questions($id:String!,$in:UGC_DiscussionInput!){" + VIEWER + ","
    "product(filter:{id:$id}){id,shortName,slug,counters{discussions},"
    "discussion(input:$in){__typename,... on UGC_DiscussionPageConnection{" + PAGE_INFO + ","
    "items{id,depth,creationDate,text,isBest,isBuyer,parent{id},"
    "author{b2c{__typename,... on B2C_PublicUserB2C{expert{isExpert}}},"
    "vendor{__typename,... on B2C_Vendor{brand{name}}}},"
    "voteInfo{info{counters{likes,dislikes}}}}}}"
    "}}"
)

DISCOUNT_FILTER = (
    "query Markdown($in:CatalogFilter_ProductsFilterInput!){" + VIEWER + ","
    "discountFilter(filter:$in){record{"
    "products{" + SNIPPET + "," + PROPERTIES + ",delivery{" + PICKUP_DAYS + "}},"
    + PAGE_INFO + "," + SORTINGS + "},"
    + LISTING_ERROR + "}}"
)

DISCOUNTED_COPIES = (
    "query MarkdownCopies($id:String!){" + VIEWER + ","
    "product(filter:{id:$id}){id,shortName,slug,isDiscount,originalProductId,counters{discountedProducts},"
    "recommendations{discountedProducts{" + SNIPPET + "," + PROPERTIES + ",delivery{" + PICKUP_DAYS + "}}}"
    "}}"
)

CITIES = "query Cities{citiesForSelect{groupedByLetter{cities{id,name,spaceId,region,isMainInRegion}}}}"
